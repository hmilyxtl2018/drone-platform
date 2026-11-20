-- Expose the existing UAV Agent Profiles through the bounded DSH Gateway.
-- Asset reads remain read-only and Runs retain the normal Engine review gates.
INSERT INTO agent_dsh_exposure_policy(agent_key,enabled,allowed_modes_json)
SELECT agent_key,true,'["unattended-draft"]'::jsonb
  FROM agent_definition
 WHERE agent_key IN ('uav-capability-analyst','uav-design-engineer','uav-evidence-reviewer')
ON CONFLICT(agent_key) DO UPDATE SET enabled=EXCLUDED.enabled,
  allowed_modes_json=EXCLUDED.allowed_modes_json,updated_at=now();
