-- P0-A runtime truth gates. This migration does not claim that an executor
-- exists; it keeps the Future Battlefield Profile in testing until reviewed
-- executor implementations and the preflight checks are present.

UPDATE agent_definition
SET status='testing'
WHERE agent_key='future-battlefield-analyst'
  AND status NOT IN ('disabled','inactive','retired');

UPDATE agent_model_policy
SET config_json=(config_json::jsonb ||
    '{"runtimeTruthGate":"fixed-runtime-preflight-v1","researchModeAvailable":false}'::jsonb)::text
WHERE agent_key='future-battlefield-analyst';

INSERT INTO agent_validation_rule(rule_id,agent_key,rule_type,expression,severity)
VALUES ('future-runtime-preflight','future-battlefield-analyst','runtime-preflight',
        'every fixed node must resolve to a reviewed executor before Run creation','blocking')
ON CONFLICT (rule_id) DO NOTHING;
