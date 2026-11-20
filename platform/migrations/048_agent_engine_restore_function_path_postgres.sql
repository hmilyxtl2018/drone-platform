-- pg_restore deliberately clears the session search path. The recursive JSON
-- CHECK helper must retain its own schema while table data are restored.
-- Capture the migration's current schema so isolated verification schemas work
-- as well as the public admin database.
DO $$
DECLARE target_schema text := current_schema();
BEGIN
    EXECUTE format('ALTER FUNCTION %I.agent_phase3_no_secrets(jsonb) SET search_path TO %I,pg_catalog',
                   target_schema,target_schema);
END;
$$;
