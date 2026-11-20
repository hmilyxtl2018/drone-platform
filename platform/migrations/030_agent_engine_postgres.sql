-- PostgreSQL-only runtime completion for ontology_agent_admin.
-- Migrations 025-029 remain the SQLite source for the local standalone file;
-- this script is idempotent and fills the PostgreSQL policy/schema gaps used by
-- the Agent Engine.

INSERT INTO agent_resource_policy(agent_key,max_parallel_tools,max_retries_per_tool,permitted_risk_levels_json)
SELECT agent_key,4,2,'["low","medium"]'::jsonb FROM agent_definition
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_approval_policy(agent_key,approval_points_json,approver_roles_json)
SELECT agent_key,'[]','["engineer"]' FROM agent_definition
ON CONFLICT (agent_key) DO NOTHING;

-- The gateway still supplies an adapter-level fallback, but keeping schemas in
-- the registry makes the admin console and runtime contract agree.
UPDATE tool_registry
SET input_schema='{"type":"object","required":["input","state"],"properties":{"input":{"type":"object"},"state":{"type":"object"}}}'::jsonb
WHERE tool_key IN (
  'uav.get_profile','uav.get_capability_baseline','uav.query_flight_logs',
  'uav.get_mission_summary','uav.calculate_energy_consumption',
  'uav.get_battery_health','uav.check_requirement_compliance',
  'uav.find_capability_gap'
);

UPDATE tool_registry SET output_schema='{"type":"object","required":["uavId","profile","evidence"]}'::jsonb WHERE tool_key='uav.get_profile';
UPDATE tool_registry SET output_schema='{"type":"object","required":["uavId","capabilities","evidence"]}'::jsonb WHERE tool_key='uav.get_capability_baseline';
UPDATE tool_registry SET output_schema='{"type":"object","required":["records","recordCount","evidence"]}'::jsonb WHERE tool_key='uav.query_flight_logs';
UPDATE tool_registry SET output_schema='{"type":"object","required":["missions","missionCount","evidence"]}'::jsonb WHERE tool_key='uav.get_mission_summary';
UPDATE tool_registry SET output_schema='{"type":"object","required":["energyUsedWh","sampleCount","evidence"]}'::jsonb WHERE tool_key='uav.calculate_energy_consumption';
UPDATE tool_registry SET output_schema='{"type":"object","required":["soh","risk","evidence"]}'::jsonb WHERE tool_key='uav.get_battery_health';
UPDATE tool_registry SET output_schema='{"type":"object","required":["checks","compliant","evidence"]}'::jsonb WHERE tool_key='uav.check_requirement_compliance';
UPDATE tool_registry SET output_schema='{"type":"object","required":["gaps","hasGap","evidence"]}'::jsonb WHERE tool_key='uav.find_capability_gap';
