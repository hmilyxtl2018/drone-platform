-- P0-C structured evidence references. Existing candidate reports remain
-- immutable; the stricter contract applies to new Future Battlefield Runs.

UPDATE agent_output_contract
SET schema_json=jsonb_set(
    schema_json::jsonb,
    '{properties,findings,items,required}',
    '["statement","supportingClaims","conditions","counterArguments","unknowns","supportingEvidence"]'::jsonb,
    true
 )::text
WHERE agent_key='future-battlefield-analyst'
  AND schema_json::jsonb ? 'properties';

UPDATE agent_output_contract
SET schema_json=jsonb_set(
    schema_json::jsonb,
    '{properties,findings,items,properties,supportingEvidence}',
    '{"type":"array","items":{"type":"object","required":["evidenceId","sourceArtifactId","contentHash","locator","truthStatus"],"properties":{"evidenceId":{"type":"string","minLength":1},"sourceArtifactId":{"type":"string","minLength":1},"contentHash":{"type":"string","pattern":"^[0-9a-f]{64}$"},"locator":{"type":"object"},"truthStatus":{"enum":["unverified","human-reviewed-unverified","verified"]}},"additionalProperties":false}}'::jsonb,
    true
 )::text
WHERE agent_key='future-battlefield-analyst'
  AND schema_json::jsonb ? 'properties';

INSERT INTO agent_validation_rule(rule_id,agent_key,rule_type,expression,severity)
VALUES ('future-evidence-reference-integrity','future-battlefield-analyst','evidence-reference-integrity',
        'every substantive finding must reference stored evidence IDs, artifact hashes and locators','blocking')
ON CONFLICT (rule_id) DO NOTHING;
