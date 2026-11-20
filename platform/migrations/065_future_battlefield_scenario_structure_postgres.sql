-- P1-A completion: scenario_structure Action returns a bounded candidate
-- structure and the application report preserves it as unverified. Tool
-- versions are append-only, so the change is published as 1.2.0.
DO $$
DECLARE schema_json jsonb;
BEGIN
    SELECT jsonb_set(output_schema::jsonb,'{properties,scenarioStructure}',
        '{"type":"object","required":["scenarios","method","truthStatus"],"properties":{"scenarios":{"type":"array"},"method":{"const":"evidence-bounded-projection"},"truthStatus":{"const":"unverified"}},"additionalProperties":false}'::jsonb,true)
      INTO schema_json
      FROM tool_version WHERE tool_key='analysis.scenario_structure' AND version='1.1.0';
    IF schema_json IS NOT NULL THEN
        INSERT INTO tool_version(tool_key,version,input_schema,output_schema,changelog,status)
        SELECT tool_key,'1.2.0',input_schema,schema_json,
               'Preserve evidence-bounded scenario structure; never certify military outcomes','published'
          FROM tool_version WHERE tool_key='analysis.scenario_structure' AND version='1.1.0'
        ON CONFLICT (tool_key,version) DO NOTHING;
        UPDATE tool_registry
           SET input_schema=(SELECT input_schema FROM tool_version WHERE tool_key='analysis.scenario_structure' AND version='1.2.0'),
               output_schema=schema_json, current_version='1.2.0'
         WHERE tool_key='analysis.scenario_structure';
        UPDATE agent_tool_binding
           SET tool_version='1.2.0'
         WHERE agent_key IN ('future-battlefield-analyst','evidence-collection-action','evidence-extraction-action',
                             'trend-analysis-action','scenario-analysis-action','red-team-action')
           AND tool_key='analysis.scenario_structure';
    END IF;
END;
$$;

UPDATE agent_output_contract
SET schema_json=jsonb_set(
      jsonb_set(schema_json::jsonb,
                '{required}',
                (schema_json::jsonb->'required') || '["scenarioStructure"]'::jsonb,
                true),
      '{properties,scenarioStructure}',
      '{"type":"object","required":["scenarios","method","truthStatus","gaps"],"properties":{"scenarios":{"type":"array"},"method":{"const":"evidence-bounded-projection"},"truthStatus":{"const":"unverified"},"gaps":{"type":"array"}},"additionalProperties":false}'::jsonb,
      true)::text
WHERE agent_key='future-battlefield-analyst';
