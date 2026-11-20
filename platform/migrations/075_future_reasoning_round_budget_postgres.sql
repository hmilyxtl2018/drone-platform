-- Migration 070 seeded a whole-Run allowance of four rounds for eleven
-- required stages. Each stage entry charges one round; maxRounds=4 is a
-- separate per-stage retry ceiling. Append a corrected policy version only
-- for that untouched seed. Historical Run snapshots and custom policies stay
-- immutable, and reapplying this migration is a no-op.
SELECT pg_advisory_xact_lock(hashtextextended('agent-admin-profile-config',0));

WITH seed AS (
    SELECT p.*, (SELECT sum((stage->>'maxRounds')::integer)
                   FROM jsonb_array_elements(p.policy_json->'stages') stage) AS total_rounds
      FROM agent_reasoning_policy p
      JOIN agent_definition d ON d.agent_key=p.agent_key AND d.current_version=p.profile_version
     WHERE p.policy_id='reasoning-future-battlefield-analyst-v1'
       AND p.version=1 AND p.created_by='migration-070'
       AND p.policy_json->'budgetLimits'->>'reasoning_rounds'='4'
       AND NOT EXISTS (SELECT 1 FROM agent_reasoning_policy newer
                        WHERE newer.agent_key=p.agent_key AND newer.version>p.version)
)
INSERT INTO agent_reasoning_policy(policy_id,agent_key,profile_version,version,policy_json,created_by)
SELECT 'reasoning-future-battlefield-analyst-v2-round-budget',agent_key,profile_version,2,
       jsonb_set(policy_json || jsonb_build_object(
           'policyId','reasoning-future-battlefield-analyst-v2-round-budget','version',2),
           '{budgetLimits,reasoning_rounds}',to_jsonb(total_rounds)), 'migration-075'
  FROM seed WHERE total_rounds>4
ON CONFLICT(agent_key,version) DO NOTHING;

INSERT INTO agent_reasoning_stage(policy_id,stage_key,sequence_no,stage_json)
SELECT p.policy_id,stage->>'key',(stage->>'sequence')::integer,stage
  FROM agent_reasoning_policy p
 CROSS JOIN LATERAL jsonb_array_elements(p.policy_json->'stages') stage
 WHERE p.policy_id='reasoning-future-battlefield-analyst-v2-round-budget'
ON CONFLICT(policy_id,stage_key) DO NOTHING;
