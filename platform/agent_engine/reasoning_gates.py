"""Named, deterministic quality gates for evidence-bounded reasoning.

These checks establish traceability and declared coverage, never the truth of
external statements. They evaluate typed objects; policies cannot inject code or
expressions. Model agreement is kept separate from independent corroboration.
"""
from __future__ import annotations

import re
import hashlib
import json
from copy import deepcopy


HEX_HASH = re.compile(r"^[a-f0-9]{64}$")
DOMAIN_TYPES = ("system", "capability", "technology", "constraint", "context", "outcome")
DOMAIN_PREDICATES = ("depends-on", "constrains", "enables", "changes", "part-of", "associated-with")
SCENARIO_DIMENSIONS = ("drivers", "adversaryResponses", "deceptionAndEwCyber", "systemDegradation",
                       "logisticsConstraints", "triggerIndicators", "counterfactuals")
GATE_CATALOG = {
    "question-bounded": {"severity": "blocker", "onFail": "stop", "affectsPublication": True},
    "ontology-contract-valid": {"severity": "blocker", "onFail": "keep-as-candidate", "affectsPublication": True},
    "source-allowlist-valid": {"severity": "blocker", "onFail": "exclude-source", "affectsPublication": True},
    "evidence-hash-valid": {"severity": "blocker", "onFail": "exclude-evidence", "affectsPublication": True},
    "quote-locator-valid": {"severity": "blocker", "onFail": "exclude-evidence", "affectsPublication": True},
    "publication-date-status-known-or-explicitly-unknown": {"severity": "warning", "onFail": "record-gap", "affectsPublication": True},
    "publication-date-status": {"severity": "warning", "onFail": "record-gap", "affectsPublication": True},
    "temporal-coverage": {"severity": "warning", "onFail": "reduce-time-confidence", "affectsPublication": True},
    "relation-endpoints-valid": {"severity": "blocker", "onFail": "keep-as-candidate", "affectsPublication": True},
    "relation-evidence-bound": {"severity": "blocker", "onFail": "keep-as-candidate", "affectsPublication": True},
    "relation-evidence-binding": {"severity": "blocker", "onFail": "keep-as-candidate", "affectsPublication": True},
    "relation-support-reviewed": {"severity": "blocker", "onFail": "keep-as-candidate", "affectsPublication": True},
    "independent-source-checked": {"severity": "warning", "onFail": "request-evidence", "affectsPublication": True},
    "counterargument-present": {"severity": "warning", "onFail": "request-evidence", "affectsPublication": True},
    "unknowns-present": {"severity": "warning", "onFail": "record-gap", "affectsPublication": True},
    "conditions-present": {"severity": "warning", "onFail": "record-gap", "affectsPublication": True},
    "indicators-present": {"severity": "warning", "onFail": "request-evidence", "affectsPublication": True},
    "scenario-dimensions-complete": {"severity": "warning", "onFail": "record-gap", "affectsPublication": True},
    "publication-review-complete": {"severity": "blocker", "onFail": "withhold-publication", "affectsPublication": True},
}
GATE_ACTIONS = frozenset(item['onFail'] for item in GATE_CATALOG.values())


def normalize_quality_gates(definitions, *, stage_keys=None, strict=True):
    """Validate the authoring contract before any policy can be persisted."""
    if not isinstance(definitions, list) or len(definitions) > 100:
        raise ValueError('qualityGates must be a bounded list')
    output, seen = [], set()
    allowed = {'gateId', 'gateKey', 'id', 'stages', 'stageKeys', 'enabled', 'severity',
               'onFail', 'affectsPublication', 'maxLength', 'minimumSources', 'dimensions'}
    for definition in definitions:
        if not isinstance(definition, (str, dict)):
            raise ValueError('quality gate must be a name or object')
        config = {'gateId': definition} if isinstance(definition, str) else dict(definition)
        aliases = [config[key] for key in ('gateId', 'gateKey', 'id') if key in config]
        gate_id = aliases[0] if aliases else None
        if not isinstance(gate_id, str) or not gate_id or gate_id.strip() != gate_id or len(gate_id) > 240:
            raise ValueError('quality gate identities must be nonempty strings')
        if any(value != gate_id for value in aliases) or gate_id in seen:
            raise ValueError('quality gate identities must be unambiguous and unique')
        seen.add(gate_id)
        if gate_id not in GATE_CATALOG:
            if strict:
                raise ValueError('quality gate is not implemented')
            # Unknown gates cannot be disabled, scoped away or downgraded.
            output.append({'gateId': gate_id, 'severity': 'blocker', 'onFail': 'stop', 'affectsPublication': True})
            continue
        if set(config) - allowed:
            raise ValueError('unsupported quality gate configuration field')
        if 'stages' in config and 'stageKeys' in config and config['stages'] != config['stageKeys']:
            raise ValueError('ambiguous quality gate stage selection')
        selected = config.get('stages', config.get('stageKeys'))
        if selected is not None:
            if (not isinstance(selected, list) or not selected or len(selected) > 100
                    or any(not isinstance(key, str) or not key or key.strip() != key for key in selected)
                    or len(set(selected)) != len(selected)):
                raise ValueError('quality gate stages must be unique nonempty names')
            if stage_keys is not None and not set(selected) <= set(stage_keys):
                raise ValueError('quality gate selects an unknown reasoning stage')
        for key in ('enabled', 'affectsPublication'):
            if key in config and type(config[key]) is not bool:
                raise ValueError('quality gate ' + key + ' must be boolean')
        if config.get('severity', 'blocker') not in {'blocker', 'warning', 'info'}:
            raise ValueError('unsupported quality gate severity')
        if 'onFail' in config and config['onFail'] not in GATE_ACTIONS:
            raise ValueError('unsupported quality gate failure action')
        for key, expected_gate, cap in (('maxLength', 'question-bounded', 100000),
                                        ('minimumSources', 'independent-source-checked', 500)):
            if key in config and (gate_id != expected_gate or type(config[key]) is not int or not 1 <= config[key] <= cap):
                raise ValueError('invalid quality gate ' + key)
        if 'dimensions' in config:
            values = config['dimensions']
            if (gate_id != 'scenario-dimensions-complete' or not isinstance(values, list) or not values
                    or any(not isinstance(value, str) or value not in SCENARIO_DIMENSIONS for value in values)
                    or len(set(values)) != len(values)):
                raise ValueError('invalid scenario gate dimensions')
        output.append({**GATE_CATALOG[gate_id], **config, 'gateId': gate_id})
    return output


def relation_review_binding(row):
    """Bind review proof to the exact relation, perspective and evidence set."""
    def endpoint(value):
        return value.get('id') if isinstance(value, dict) else value
    value = {'perspectiveKey': row.get('perspectiveKey'), 'relationId': row.get('id', row.get('relationId')),
             'subject': endpoint(row.get('subject')), 'predicate': row.get('predicate'),
             'object': endpoint(row.get('object')), 'evidenceIds': sorted(set(_ids(row.get('evidenceIds'))))}
    if row.get('findingId'):
        value.update({k: deepcopy(row.get(k)) for k in
                      ('findingId', 'epistemicKind', 'modality', 'conditions', 'counterArguments', 'unknowns')})
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _identity(row, fallback="unknown"):
    return str(row.get("id") or row.get("relationId") or row.get("candidateId")
               or row.get("perspectiveKey") or row.get("evidenceId") or row.get("claimId") or fallback)


def _ids(values):
    return [str(value) for value in _list(values) if isinstance(value, (str, int)) and not isinstance(value, bool)]


def _hash(value):
    return bool(isinstance(value, str) and HEX_HASH.fullmatch(value))


class QualityGateEngine:
    """Evaluate only configured named gates against one immutable stage result."""

    catalog = GATE_CATALOG

    def evaluate(self, policy, *, stage_key, payload, context=None):
        if not isinstance(policy, dict) or not isinstance(payload, dict):
            raise ValueError("reasoning policy and stage payload must be objects")
        if not isinstance(stage_key, str) or not stage_key:
            raise ValueError("stage key is required")
        context = _dict(context)
        report = _dict(payload.get("judgmentReport")) or payload
        material = {**context, **report}
        # Trusted evidence is a separate input supplied by the runtime. A model
        # cannot make a forged hash authoritative by repeating it in its output.
        evidence = _list(report.get("evidence")) or _list(context.get("evidence"))
        trusted = _list(context.get("validatedEvidence"))
        findings = (_list(report.get("findings")) or _list(report.get("perspectiveFindings"))
                    or _list(context.get("findings")))
        ontology = _dict(report.get("ontology"))
        relations = (_list(ontology.get("assertions")) or _list(report.get("candidateRelations"))
                     or _list(report.get("relationCandidates")) or _list(context.get("candidateRelations")))
        if not relations:
            relations = [{**row, "perspectiveKey": row.get("perspectiveKey", finding.get("perspectiveKey")),
                          "_entities": finding.get("entities", [])}
                         for finding in findings for row in _list(finding.get("relations")) if isinstance(row, dict)]
        definitions = policy.get("qualityGates", policy.get("gates", []))
        definitions = normalize_quality_gates(definitions, strict=False)
        results = []
        seen = set()
        for definition in definitions:
            config = definition
            gate_id = config.get("gateId") or config.get("gateKey") or config.get("id")
            if not isinstance(gate_id, str) or not gate_id or gate_id in seen:
                raise ValueError("quality gate identities must be nonempty and unique")
            seen.add(gate_id)
            stages = config.get("stages", config.get("stageKeys"))
            if stages is not None and (not isinstance(stages, list) or stage_key not in stages):
                continue
            if config.get("enabled", True) is False:
                continue
            settings = {**GATE_CATALOG.get(gate_id, {}), **config}
            severity = settings.get("severity", "blocker")
            if severity not in {"blocker", "warning", "info"}:
                raise ValueError("unsupported quality gate severity")
            if gate_id not in GATE_CATALOG:
                status, reason, details, refs = "failed", "gate-not-implemented", {"gateId": gate_id}, []
            else:
                status, reason, details, refs = self._check(gate_id, policy, settings, material,
                    evidence, trusted, findings, relations, ontology, context)
            results.append({"gateId": gate_id, "stageKey": stage_key, "status": status,
                "severity": severity, "action": settings.get("onFail", "record-gap") if status == "failed" else "continue",
                "affectsPublication": bool(settings.get("affectsPublication", True)), "reason": reason,
                "evidenceIds": sorted(set(refs)), "details": details, "truthVerified": False,
                "blocking": status == "failed" and severity == "blocker" and bool(settings.get("affectsPublication", True))})
        return results

    @staticmethod
    def _check(gate_id, policy, settings, material, evidence, trusted, findings, relations, ontology, context):
        def outcome(errors, passed="contract-satisfied", **details):
            return ("failed", "contract-incomplete", {"issues": errors, **details}, []) if errors else ("passed", passed, details, [])

        def missing(kind):
            return "not-applicable", "no-" + kind + "-at-this-stage", {}, []

        if gate_id == "question-bounded":
            task = (_dict(context.get("taskContract")) or _dict(context.get("input"))
                    or _dict(material.get("taskDefinition")) or _dict(material.get("taskContract"))
                    or _dict(material.get("input")) or material)
            question = task.get("question", task.get("objective"))
            maximum = settings.get("maxLength", 10000)
            return outcome([] if isinstance(question, str) and 1 <= len(question.strip()) <= maximum
                           else [{"field": "question", "reason": "missing-or-unbounded"}])

        if gate_id == "source-allowlist-valid":
            from urllib.parse import urlsplit
            source_policy = _dict(context.get("sourcePolicy")) or _dict(policy.get("sourcePolicy"))
            allowed = source_policy.get("allowedDomains", policy.get("allowedDomains", []))
            sources = _list(_dict(material.get("sourceBundle")).get("sources")) or evidence
            if not sources:
                return missing("sources")
            issues = []
            for row in sources:
                uri = row.get("sourceUri", "")
                try:
                    parsed = urlsplit(uri)
                    host = (parsed.hostname or "").lower()
                    accepted = parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in (None, 443)
                    accepted = accepted and any(host == domain or
                        (source_policy.get("includeSubdomains", False) and host.endswith("." + domain)) for domain in allowed)
                except (ValueError, TypeError):
                    accepted = False
                if not accepted:
                    issues.append({"sourceUri": uri, "reason": "outside-frozen-source-policy"})
            return outcome(issues)

        if gate_id in {"evidence-hash-valid", "quote-locator-valid"}:
            if not evidence:
                return missing("evidence")
            trusted_by_id = {str(row.get("evidenceId") or row.get("claimId")): row for row in trusted}
            issues = []
            for row in evidence:
                identity = _identity(row)
                fields = ("sourceArtifactHash", "contentHash") if gate_id == "evidence-hash-valid" else ("quote", "sourceUri", "locator")
                bad = [name for name in fields if not row.get(name)]
                if gate_id == "evidence-hash-valid":
                    bad += [name for name in fields if row.get(name) and not _hash(row[name])]
                else:
                    locator = _dict(row.get("locator"))
                    start, end = locator.get("start", row.get("startOffset")), locator.get("end", row.get("endOffset"))
                    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
                        bad.append("locator-range")
                    elif isinstance(row.get("quote"), str) and end - start != len(row["quote"]):
                        bad.append("locator-quote-length")
                    if isinstance(row.get("body"), str) and type(start) is int and type(end) is int and row["body"][start:end] != row.get("quote"):
                        bad.append("body-quote-mismatch")
                baseline = trusted_by_id.get(identity)
                if baseline is None:
                    bad.append("validated-evidence-absent")
                elif any(row.get(name) != baseline.get(name) for name in fields):
                    bad.append("validated-evidence-mismatch")
                if bad:
                    issues.append({"evidenceId": identity, "fields": sorted(set(bad))})
            return outcome(issues, passed="matches-validated-evidence", evidenceCount=len(evidence))

        if gate_id in {"publication-date-status", "publication-date-status-known-or-explicitly-unknown", "temporal-coverage"}:
            if not evidence:
                return missing("evidence")
            unknown = [_identity(row) for row in evidence if not row.get("publishedAt")]
            if gate_id == "temporal-coverage":
                issues = [{"evidenceId": identity, "reason": "publication-time-unknown"} for identity in unknown]
            else:
                issues = [{"evidenceId": _identity(row), "reason": "date-status-missing-or-inconsistent"}
                          for row in evidence if (row.get("publicationDateStatus") not in {"known", "unknown"}
                            or (row.get("publicationDateStatus") == "known" and not row.get("publishedAt"))
                            or (row.get("publicationDateStatus") == "unknown" and row.get("publishedAt")))]
            return outcome(issues, unknownCount=len(unknown), knownCount=len(evidence) - len(unknown))

        if gate_id in {"ontology-contract-valid", "relation-endpoints-valid"}:
            contract = _dict(context.get("ontologyContract")) or _dict(policy.get("ontologyContract"))
            types = set(contract.get("entityTypes", DOMAIN_TYPES))
            predicates = contract.get("predicates", DOMAIN_PREDICATES)
            predicates = set(predicates.keys() if isinstance(predicates, dict) else predicates)
            if not relations and not ontology:
                return missing("relations")
            nodes = {row.get("id"): row for row in _list(ontology.get("nodes")) if isinstance(row, dict)}
            issues = []
            for row in relations:
                entities = {item.get("id"): item for item in _list(row.get("_entities")) if isinstance(item, dict)}
                available = entities or nodes or {item.get("id"): item for item in _list(material.get("entities")) if isinstance(item, dict)}
                invalid = [key for key in ("subject", "object") if row.get(key) not in available
                           or available[row[key]].get("type") not in types]
                if row.get("predicate") not in predicates:
                    invalid.append("predicate")
                if invalid:
                    issues.append({"relationId": _identity(row), "fields": invalid})
            for edge in _list(ontology.get("edges")):
                if edge.get("subject") not in nodes or edge.get("object") not in nodes:
                    issues.append({"reason": "edge-endpoint-unknown"})
            return outcome(issues, relationCount=len(relations))

        if gate_id in {"relation-evidence-bound", "relation-evidence-binding"}:
            if not relations:
                return missing("relations")
            known = {str(row.get("evidenceId") or row.get("claimId")) for row in trusted}
            issues, refs = [], []
            for row in relations:
                values = row.get("evidenceIds")
                if not isinstance(values, list) or not values or any(not isinstance(value, str) or value not in known for value in values):
                    issues.append({"relationId": _identity(row), "reason": "unknown-or-empty-evidence"})
                else:
                    refs.extend(values)
            status, reason, details, _ = outcome(issues, relationCount=len(relations))
            return status, reason, details, refs

        if gate_id == "relation-support-reviewed":
            if not relations:
                return missing("relations")
            issues, independence = [], set()
            proofs = {row.get('bindingHash'): row for row in _list(context.get('validatedSupportReviews'))
                      if isinstance(row, dict) and row.get('verdict') == 'supported'}
            known = {str(row.get("evidenceId") or row.get("claimId")) for row in trusted}
            for row in relations:
                review = _dict(row.get("supportReview"))
                proof = proofs.get(relation_review_binding(row))
                if not proof or not all(proof.get(key) for key in ('reviewCallId', 'reviewArtifactId', 'generatorCallId',
                                                                   'generatorArtifactId', 'generatorModel', 'reviewerModel')):
                    issues.append({"relationId": _identity(row), "reason": "persisted-support-review-proof-missing"})
                else:
                    independence.add(proof.get('reviewIndependence', 'not-established'))
                    if (review.get('generatorCallId') != proof['generatorCallId']
                            or review.get('reviewCallId') != proof['reviewCallId']
                            or review.get('reviewArtifactId') != proof['reviewArtifactId']):
                        issues.append({"relationId": _identity(row), "reason": "support-review-proof-mismatch"})
                    if review.get('method') == 'independent-model-review' and proof['generatorModel'] == proof['reviewerModel']:
                        issues.append({"relationId": _identity(row), "reason": "same-model-cannot-be-independent"})
                if row.get("truthStatus", "unverified") != "unverified":
                    issues.append({"relationId": _identity(row), "reason": "unsupported-truth-promotion"})
                refs, reviewed = _ids(row.get("evidenceIds")), _ids(review.get("evidenceIds"))
                if not refs or not reviewed or set(refs) != set(reviewed) or not set(refs) <= known:
                    issues.append({"relationId": _identity(row), "reason": "support-review-evidence-binding-missing"})
            return outcome(issues, reviewIndependence=next(iter(independence)) if len(independence) == 1
                           else "mixed-or-not-established", truthVerified=False)

        supported_findings = [row for row in findings if row.get("status") != "insufficient-evidence"]
        if gate_id in {"counterargument-present", "unknowns-present", "conditions-present", "indicators-present"}:
            if not findings:
                return missing("findings")
            field = {"counterargument-present": "counterArguments", "unknowns-present": "unknowns",
                     "conditions-present": "conditions", "indicators-present": "triggerIndicators"}[gate_id]
            issues = []
            for row in supported_findings:
                values = row.get(field, _dict(row.get("scenarioCard")).get(field))
                if not isinstance(values, list) or not values or any(not isinstance(item, (str, dict)) or not item for item in values):
                    issues.append({"perspectiveKey": row.get("perspectiveKey"), "field": field})
            return outcome(issues)

        if gate_id == "independent-source-checked":
            if not findings:
                return missing("findings")
            by_id = {str(row.get("evidenceId") or row.get("claimId")): row for row in trusted}
            minimum = settings.get("minimumSources", _dict(policy.get("qualityBar")).get("minimumIndependentSources", 2))
            issues = []
            for row in supported_findings:
                groups = set()
                for identity in _ids(row.get("supportingClaims")):
                    item = by_id.get(identity, {})
                    # Distinct domains are not proof of editorial independence.
                    # Only explicit reviewed source grouping can satisfy this gate.
                    if item.get("independenceStatus") in {"independent", "reviewed-independent"}:
                        group = item.get("publisherId") or item.get("independentSourceGroup")
                        if group:
                            groups.add(str(group))
                if len(groups) < minimum:
                    issues.append({"perspectiveKey": row.get("perspectiveKey"), "independentSourceCount": len(groups), "required": minimum})
            return outcome(issues, independenceBasis="explicit-reviewed-origin-groups")

        if gate_id == "scenario-dimensions-complete":
            if not findings:
                return missing("findings")
            required = settings.get("dimensions", SCENARIO_DIMENSIONS)
            issues = []
            for row in supported_findings:
                card = _dict(row.get("scenarioCard")) or _dict(row.get("scenarioStructure"))
                absent = [key for key in required if not card.get(key) or
                          (isinstance(card.get(key), dict) and not any(card[key].values()))]
                if absent:
                    issues.append({"perspectiveKey": row.get("perspectiveKey"), "dimensions": absent})
            return outcome(issues)

        if gate_id == "publication-review-complete":
            approval = _dict(context.get("publicationApproval"))
            valid = approval.get("status") == "approved" and approval.get("reviewerId") and approval.get("persisted") is True
            return outcome([] if valid else [{"reason": "persisted-human-publication-approval-required"}])

        return "failed", "gate-not-implemented", {}, []


def gate_summary(results):
    """Small graph/UI projection; failed warnings remain visible as gaps."""
    failed = [row for row in results if row.get("status") == "failed"]
    return {"total": len(results), "passed": sum(row.get("status") == "passed" for row in results),
            "failed": len(failed), "blockers": sum(row.get("severity") == "blocker" for row in failed),
            "publicationBlocked": any(row.get("affectsPublication") for row in failed)}
