"""Reviewed identity of the document/ontology research suite.

This registry grants no authority: admission, tool grants and quotas still apply.
"""
APPLICATION_KEY = 'ontology-cognitive-analyst'
ACTION_KEYS = frozenset({'situation-assessment-action', 'rule-applicability-action',
                         'tactics-research-action', 'ontology-consistency-action'})
AGENT_KEYS = ACTION_KEYS | {APPLICATION_KEY}
NODE_IDS = (
    'freeze-context', 'acquire-sources', 'deduplicate-sources', 'extract-claims',
    'map-candidates', 'validate-inputs', 'assess-situation', 'assess-rules',
    'read-capability-evidence', 'compare-options', 'check-consistency',
    'synthesize-findings', 'build-explanation-graph', 'validate-delivery',
    'deliver-review-draft', 'validate-situation-input', 'align-observations',
    'derive-state-changes', 'verify-state-evidence', 'package-situation',
    'validate-rule-input', 'resolve-rule-times', 'interpret-applicability',
    'evaluate-formal-constraints', 'verify-rule-evidence', 'package-rules',
    'validate-comparison-input', 'compare-research-options', 'review-counterevidence',
    'verify-comparison-evidence', 'package-comparison', 'validate-consistency-input',
    'check-schema-shapes', 'check-temporal-constraints', 'check-relation-constraints',
    'explain-view-conflicts', 'package-consistency',
)
