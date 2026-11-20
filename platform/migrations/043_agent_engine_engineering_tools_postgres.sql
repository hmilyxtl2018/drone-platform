-- Versioned deterministic tools. Schemas are fixed to formula 1.0.0.
-- Runtime source/workspace checks are stricter than JSON shape validation.
-- This migration does not alter historical Run snapshots or source SQLite.
DO $$
DECLARE
    key text;
    calculation jsonb;
    input_contract jsonb;
    output_contract jsonb := '{"type":"object","required":["formula","formulaVersion","inputSnapshot","inputHash","runInputHash","serverProvenance","artifactId","artifactHash","truthVerified","reviewedCompliance","engineeringCertification"],"properties":{"formulaVersion":{"const":"1.0.0"},"truthVerified":{"const":false},"reviewedCompliance":{"const":"unknown"},"engineeringCertification":{"const":false}}}';
    basis jsonb := '{"type":"object","required":["kind"],"properties":{"kind":{"enum":["assumption","reported-specification","measurement"]},"description":{"type":"string","minLength":1,"maxLength":1000},"sourceId":{"type":"string","minLength":1,"maxLength":1000},"sourceVersion":{"type":"string","minLength":1,"maxLength":1000},"contentHash":{"type":"string","pattern":"^[0-9a-f]{64}$"}},"additionalProperties":false}';
    quantity jsonb;
    conditions jsonb;
    common_properties jsonb;
    common_envelope jsonb;
BEGIN
    quantity := jsonb_build_object('type','object','required',jsonb_build_array('unit'),'properties',
        jsonb_build_object('value',jsonb_build_object('type','number','minimum',0),
            'min',jsonb_build_object('type','number','minimum',0),'max',jsonb_build_object('type','number','minimum',0),
            'unit',jsonb_build_object('enum',jsonb_build_array('kg','g','lb','s','min','h','W','kW','Wh','kWh','J','kJ','m','km','ft','nmi','m/s','km/h','kn','1','%')),
            'basis',basis),'oneOf','[{"required":["value"],"not":{"anyOf":[{"required":["min"]},{"required":["max"]}]}},{"required":["min","max"],"not":{"required":["value"]}}]'::jsonb,'additionalProperties',false);
    conditions := jsonb_build_object('type',jsonb_build_array('object','null'),'maxProperties',50,'additionalProperties',
        jsonb_build_object('oneOf',jsonb_build_array('{"type":"string","maxLength":1000}'::jsonb,'{"type":"null"}'::jsonb,quantity)));
    common_properties := jsonb_build_object('inputVersion','{"oneOf":[{"type":"string","minLength":1,"maxLength":1000},{"type":"integer","minimum":1}]}'::jsonb,
        'conditions',conditions,'assumptions','{"type":"array","maxItems":100,"items":{"type":"string","minLength":1,"maxLength":1000}}'::jsonb);
    common_envelope := '{"workspaceRevisionHash":{"type":"string","pattern":"^[0-9a-f]{64}$"},"candidateBindings":{"type":"array","maxItems":1000,"items":{"type":"object","required":["path","candidateId"],"properties":{"path":{"type":"string","pattern":"^(energy|averagePower|massLimit|components\\.[0-9]{1,3}\\.mass)$"},"candidateId":{"type":"string","minLength":1,"maxLength":200}},"additionalProperties":false}}}';
    FOREACH key IN ARRAY ARRAY['engineering.evaluate_requirement','engineering.mass_budget','engineering.endurance_budget'] LOOP
        IF key='engineering.evaluate_requirement' THEN
            input_contract := '{"type":"object","required":["requirementMetric"],"properties":{"requirementMetric":{"type":"string","minLength":1,"maxLength":200},"candidateId":{"type":"string","minLength":1,"maxLength":200},"workspaceRevisionHash":{"type":"string","pattern":"^[0-9a-f]{64}$"}},"additionalProperties":false}';
        ELSE
            IF key='engineering.mass_budget' THEN
                calculation := jsonb_build_object('type','object','properties',common_properties||jsonb_build_object(
                    'components',jsonb_build_object('type','array','maxItems',1000,'items',jsonb_build_object('type','object','properties',
                        jsonb_build_object('componentId','{"type":"string","minLength":1,"maxLength":1000}'::jsonb,'mass',quantity),'additionalProperties',false)),
                    'massLimit',quantity,'contingencyFraction','{"type":"number","minimum":0,"maximum":1}'::jsonb),'additionalProperties',false);
            ELSE
                calculation := jsonb_build_object('type','object','properties',common_properties||jsonb_build_object(
                    'energy',quantity,'averagePower',quantity,'usableFraction','{"type":"number","minimum":0,"maximum":1}'::jsonb,
                    'reserveFraction','{"type":"number","minimum":0,"maximum":1}'::jsonb,
                    'systemBoundary','{"type":"string","minLength":1,"maxLength":1000}'::jsonb),'additionalProperties',false);
            END IF;
            input_contract := jsonb_build_object('type','object','required',jsonb_build_array('calculation'),
                'properties',common_envelope||jsonb_build_object('calculation',calculation),'additionalProperties',false);
        END IF;
        INSERT INTO tool_registry(tool_key,tool_name,category,description,protocol,input_schema,output_schema,
            tags_json,access_mode,risk_level,status,current_version)
        VALUES(key,key,'engineering','Deterministic concept calculation; source validation required; never certification',
            'python',input_contract,output_contract,'["engineering","concept","formula-1.0.0"]','read_only','low','active','1.0.0')
        ON CONFLICT(tool_key) DO NOTHING;
        INSERT INTO tool_version(tool_key,version,input_schema,output_schema,changelog,status)
        VALUES(key,'1.0.0',input_contract,output_contract,'Immutable formula 1.0.0 and same-Run source binding','active')
        ON CONFLICT(tool_key,version) DO NOTHING;
        INSERT INTO agent_tool_binding(agent_key,tool_key,tool_version,enabled,approval_required,config_json)
        SELECT agent_key,key,'1.0.0',1,0,'{"timeoutSeconds":30,"retryLimit":0}'::jsonb FROM agent_definition
        WHERE agent_key IN ('uav-capability-analyst','uav-design-engineer','uav-evidence-reviewer')
        ON CONFLICT(agent_key,tool_key) DO NOTHING;
    END LOOP;
END;
$$;
