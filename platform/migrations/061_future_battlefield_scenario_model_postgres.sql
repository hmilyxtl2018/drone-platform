-- P1-A: structured military scenario coverage. The card records analytical
-- dimensions and unknowns; it does not assert an adversary capability.

UPDATE agent_output_contract
SET schema_json=jsonb_set(
    jsonb_set(schema_json::jsonb,
             '{required}',
             (schema_json::jsonb->'required') || '["scenarioMethodology"]'::jsonb,
             true),
    '{properties,findings,items,properties,scenarioCard}',
    '{"type":"object","required":["analysisLayer","drivers","adversaryResponses","deceptionAndEwCyber","systemDegradation","logisticsConstraints","triggerIndicators","counterfactuals","coverageStatus","gaps"],"properties":{"analysisLayer":{"enum":["strategic","operational","tactical","technical"]},"drivers":{"type":"array"},"adversaryResponses":{"type":"array"},"deceptionAndEwCyber":{"type":"object"},"systemDegradation":{"type":"array"},"logisticsConstraints":{"type":"array"},"triggerIndicators":{"type":"array"},"counterfactuals":{"type":"array"},"coverageStatus":{"enum":["complete","incomplete"]},"gaps":{"type":"array"}},"additionalProperties":false}'::jsonb,
    true
 )::text
WHERE agent_key='future-battlefield-analyst';

INSERT INTO agent_validation_rule(rule_id,agent_key,rule_type,expression,severity)
VALUES ('future-scenario-model-coverage','future-battlefield-analyst','scenario-model-coverage',
        'each enabled perspective must carry strategic/operational/tactical/technical layer and explicit response, EW/cyber, degradation, logistics, trigger and counterfactual dimensions','blocking')
ON CONFLICT (rule_id) DO NOTHING;
