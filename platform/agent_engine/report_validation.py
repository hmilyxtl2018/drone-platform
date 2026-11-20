"""Qualify model prose with the evidence service's provenance limits.

This checks explicit independence language, not the truth of arbitrary prose.
Raw model output remains in its immutable audit artifact.
"""
from __future__ import annotations

from copy import deepcopy
import re


_INDEPENDENCE_LANGUAGE = re.compile(
    r"\bindependen(?:t(?:ly)?|ce)\b|独立(?:的)?(?:来源|信息源|证据)|"
    r"(?:来源|证据).{0,12}独立|相互独立", re.IGNORECASE)
_NOTICE = ("Source independence has not been established. Distinct stored records, "
           "domains or matching values are not independent corroboration.")


def validate_report_provenance(draft, bundle):
    """Do not present unestablished source independence as a model finding.

    The current source service groups known duplicates but certifies no source
    independence. Explicit independence prose is withheld for human assessment,
    including conservative statements, rather than guessing its semantics.
    """
    result, issues = deepcopy(draft), []
    candidates = {candidate['candidateId'] for candidate in bundle['candidates']}
    for field in ('summary', 'assumptions', 'dataRequests'):
        values = [result[field]] if field == 'summary' else result[field]
        kept = []
        for index, value in enumerate(values):
            if _INDEPENDENCE_LANGUAGE.search(value):
                issues.append({'field': field, 'index': index, 'code': 'source-independence-unestablished'})
            else:
                kept.append(value)
        result[field] = (kept[0] if kept else 'Candidate research report; see evidence and open questions.') \
            if field == 'summary' else kept
    findings = []
    for index, finding in enumerate(result['findings']):
        refs = finding['candidateIds']
        if not refs or not set(refs).issubset(candidates):
            issues.append({'field': 'findings', 'index': index, 'code': 'unknown-candidate-reference'})
        elif _INDEPENDENCE_LANGUAGE.search(finding['statement']):
            issues.append({'field': 'findings', 'index': index, 'code': 'source-independence-unestablished',
                           'candidateIds': refs})
        else:
            findings.append(finding)
    result['findings'] = findings
    result['dataRequests'] = list(dict.fromkeys([*result['dataRequests'], _NOTICE]))
    result['sourceIndependence'] = {
        'status': 'unknown', 'verifiedIndependentSourceCount': 0,
        'sourceGroups': deepcopy(bundle.get('sourceGroups', [])), 'notice': _NOTICE}
    result['reportValidationIssues'] = issues
    return result
