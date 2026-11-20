-- P0 Agent Harness contract.
--
-- This migration is additive and repeatable. Existing Profile tables remain the
-- authoring surface; Harness rows make the runtime authority explicit and
-- freeze the effective grants for every new Run.

ALTER TABLE agent_definition
    ADD COLUMN IF NOT EXISTS agent_layer text NOT NULL DEFAULT 'application',
    ADD COLUMN IF NOT EXISTS runtime_kind text NOT NULL DEFAULT 'llm_loop',
    ADD COLUMN IF NOT EXISTS profile_schema_version text NOT NULL DEFAULT '1.0.0';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='agent_definition_layer_check') THEN
        ALTER TABLE agent_definition ADD CONSTRAINT agent_definition_layer_check
            CHECK (agent_layer IN ('application','action'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='agent_definition_runtime_check') THEN
        ALTER TABLE agent_definition ADD CONSTRAINT agent_definition_runtime_check
            CHECK (runtime_kind IN ('llm_loop','deterministic_action'));
    END IF;
END $$;

-- Existing Action Profiles carried their layer in model config. Promote that
-- value to a database field; old Profiles remain application Agents.
UPDATE agent_definition d
SET agent_layer = CASE WHEN EXISTS (
        SELECT 1 FROM agent_model_policy m
        WHERE m.agent_key=d.agent_key
          AND COALESCE(m.config_json::jsonb,'{}'::jsonb)->>'agentLayer'='action'
    ) THEN 'action' ELSE 'application' END,
    runtime_kind = CASE WHEN EXISTS (
        SELECT 1 FROM agent_model_policy m
        WHERE m.agent_key=d.agent_key
          AND COALESCE(m.config_json::jsonb,'{}'::jsonb)->>'agentLayer'='action'
    ) THEN 'deterministic_action' ELSE 'llm_loop' END;

ALTER TABLE tool_registry
    ADD COLUMN IF NOT EXISTS execution_kind text NOT NULL DEFAULT 'atomic_tool';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='tool_registry_execution_kind_check') THEN
        ALTER TABLE tool_registry ADD CONSTRAINT tool_registry_execution_kind_check
            CHECK (execution_kind IN ('atomic_tool','model_tool','external_tool'));
    END IF;
END $$;

ALTER TABLE tool_version
    ADD COLUMN IF NOT EXISTS side_effect_class text NOT NULL DEFAULT 'none',
    ADD COLUMN IF NOT EXISTS idempotency_required boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS network_access boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS data_classification text NOT NULL DEFAULT 'public',
    ADD COLUMN IF NOT EXISTS contract_hash text;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='tool_version_side_effect_check') THEN
        ALTER TABLE tool_version ADD CONSTRAINT tool_version_side_effect_check
            CHECK (side_effect_class IN ('none','external_read','internal_write','external_write'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='tool_version_classification_check') THEN
        ALTER TABLE tool_version ADD CONSTRAINT tool_version_classification_check
            CHECK (data_classification IN ('public','internal','restricted'));
    END IF;
END $$;

-- Existing tool_version rows are append-only. The new columns intentionally use
-- safe defaults for history; new Tool versions should provide contract_hash and
-- explicit side-effect metadata at insertion time.

CREATE TABLE IF NOT EXISTS agent_harness_contract (
    harness_contract_id text PRIMARY KEY,
    agent_key text NOT NULL REFERENCES agent_definition(agent_key),
    profile_version text NOT NULL,
    contract_version text NOT NULL,
    agent_layer text NOT NULL CHECK (agent_layer IN ('application','action')),
    runtime_kind text NOT NULL CHECK (runtime_kind IN ('llm_loop','deterministic_action')),
    invocation_mode text NOT NULL DEFAULT 'task'
        CHECK (invocation_mode IN ('task','child','scheduled','replay')),
    default_deny boolean NOT NULL DEFAULT true,
    max_child_depth integer NOT NULL DEFAULT 0 CHECK (max_child_depth>=0),
    lifecycle_policy_json jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(lifecycle_policy_json)='object'),
    failure_policy_json jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(failure_policy_json)='object'),
    memory_policy_json jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(memory_policy_json)='object'),
    side_effect_policy_json jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(side_effect_policy_json)='object'),
    contract_json jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(contract_json)='object'),
    contract_hash text NOT NULL CHECK (contract_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','testing','published','retired')),
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    UNIQUE (agent_key,profile_version,contract_version)
);

CREATE TABLE IF NOT EXISTS agent_harness_tool_grant (
    harness_contract_id text NOT NULL REFERENCES agent_harness_contract(harness_contract_id),
    tool_key text NOT NULL REFERENCES tool_registry(tool_key),
    tool_version text NOT NULL,
    permission text NOT NULL DEFAULT 'invoke'
        CHECK (permission IN ('invoke','read','write')),
    approval_required boolean NOT NULL DEFAULT false,
    input_constraint_json jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(input_constraint_json)='object'),
    grant_config_json jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(grant_config_json)='object'),
    PRIMARY KEY (harness_contract_id,tool_key,tool_version)
);

CREATE TABLE IF NOT EXISTS agent_harness_data_grant (
    grant_id text PRIMARY KEY,
    harness_contract_id text NOT NULL REFERENCES agent_harness_contract(harness_contract_id),
    resource_type text NOT NULL,
    resource_pattern text NOT NULL,
    access_mode text NOT NULL CHECK (access_mode IN ('read','write')),
    sensitivity_level text NOT NULL DEFAULT 'public'
        CHECK (sensitivity_level IN ('public','internal','restricted')),
    approval_required boolean NOT NULL DEFAULT false,
    UNIQUE (harness_contract_id,resource_type,resource_pattern,access_mode)
);

CREATE TABLE IF NOT EXISTS agent_harness_network_grant (
    network_grant_id text PRIMARY KEY,
    harness_contract_id text NOT NULL REFERENCES agent_harness_contract(harness_contract_id),
    domain_pattern text NOT NULL,
    methods_json jsonb NOT NULL DEFAULT '["GET"]'::jsonb
        CHECK (jsonb_typeof(methods_json)='array'),
    max_response_bytes bigint CHECK (max_response_bytes IS NULL OR max_response_bytes>0),
    approval_required boolean NOT NULL DEFAULT false,
    UNIQUE (harness_contract_id,domain_pattern)
);

CREATE TABLE IF NOT EXISTS agent_composition_edge (
    edge_id text PRIMARY KEY,
    parent_agent_key text NOT NULL REFERENCES agent_definition(agent_key),
    child_agent_key text NOT NULL REFERENCES agent_definition(agent_key),
    parent_profile_version text NOT NULL,
    child_profile_version text,
    handoff_schema_json jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(handoff_schema_json)='object'),
    allowed_artifact_types_json jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(allowed_artifact_types_json)='array'),
    budget_mode text NOT NULL DEFAULT 'shared'
        CHECK (budget_mode IN ('shared','child_subset','independent')),
    max_depth integer NOT NULL DEFAULT 1 CHECK (max_depth>=0),
    approval_required boolean NOT NULL DEFAULT false,
    enabled boolean NOT NULL DEFAULT true,
    CHECK (parent_agent_key<>child_agent_key),
    UNIQUE (parent_agent_key,child_agent_key,parent_profile_version)
);

CREATE TABLE IF NOT EXISTS agent_harness_step_budget (
    harness_contract_id text NOT NULL REFERENCES agent_harness_contract(harness_contract_id),
    step_key text NOT NULL,
    max_rounds integer NOT NULL DEFAULT 1 CHECK (max_rounds>=0),
    model_calls bigint NOT NULL DEFAULT 0 CHECK (model_calls>=0),
    tool_calls bigint NOT NULL DEFAULT 0 CHECK (tool_calls>=0),
    http_calls bigint NOT NULL DEFAULT 0 CHECK (http_calls>=0),
    search_calls bigint NOT NULL DEFAULT 0 CHECK (search_calls>=0),
    scrape_calls bigint NOT NULL DEFAULT 0 CHECK (scrape_calls>=0),
    input_tokens bigint NOT NULL DEFAULT 0 CHECK (input_tokens>=0),
    output_tokens bigint NOT NULL DEFAULT 0 CHECK (output_tokens>=0),
    max_parallelism integer NOT NULL DEFAULT 1 CHECK (max_parallelism>0),
    max_runtime_seconds integer NOT NULL DEFAULT 300 CHECK (max_runtime_seconds>0),
    enforcement text NOT NULL DEFAULT 'hard' CHECK (enforcement IN ('hard','soft')),
    PRIMARY KEY (harness_contract_id,step_key)
);

-- Durable per-Run/node counters. Unlike the immutable Harness ceiling, this is
-- mutable runtime usage and is updated in the same transaction as the shared
-- root budget reservation.
CREATE TABLE IF NOT EXISTS agent_run_step_budget_usage (
    run_id text NOT NULL REFERENCES agent_run(run_id),
    step_key text NOT NULL,
    used_json jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(used_json)='object'),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id,step_key)
);
CREATE INDEX IF NOT EXISTS agent_run_step_budget_usage_step_idx
    ON agent_run_step_budget_usage(step_key,updated_at);

-- The effective authority is immutable and separate from the editable Profile.
ALTER TABLE agent_run
    ADD COLUMN IF NOT EXISTS harness_contract_id text,
    ADD COLUMN IF NOT EXISTS agent_layer text,
    ADD COLUMN IF NOT EXISTS harness_snapshot_status text NOT NULL DEFAULT 'legacy';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='agent_run_layer_check') THEN
        ALTER TABLE agent_run ADD CONSTRAINT agent_run_layer_check
            CHECK (agent_layer IS NULL OR agent_layer IN ('application','action'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='agent_run_snapshot_status_check') THEN
        ALTER TABLE agent_run ADD CONSTRAINT agent_run_snapshot_status_check
            CHECK (harness_snapshot_status IN ('legacy','complete','invalid'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS agent_run_harness_snapshot (
    run_id text PRIMARY KEY REFERENCES agent_run(run_id),
    harness_contract_id text NOT NULL REFERENCES agent_harness_contract(harness_contract_id),
    contract_hash text NOT NULL CHECK (contract_hash ~ '^[0-9a-f]{64}$'),
    profile_hash text NOT NULL CHECK (profile_hash ~ '^[0-9a-f]{64}$'),
    plan_hash text NOT NULL CHECK (plan_hash ~ '^[0-9a-f]{64}$'),
    capability_graph_hash text CHECK (capability_graph_hash IS NULL OR capability_graph_hash ~ '^[0-9a-f]{64}$'),
    composition_graph_hash text CHECK (composition_graph_hash IS NULL OR composition_graph_hash ~ '^[0-9a-f]{64}$'),
    effective_tools_json jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(effective_tools_json)='array'),
    effective_data_grants_json jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(effective_data_grants_json)='array'),
    effective_network_grants_json jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(effective_network_grants_json)='array'),
    effective_child_grants_json jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(effective_child_grants_json)='array'),
    effective_limits_json jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(effective_limits_json)='object'),
    snapshot_hash text NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE agent_run_budget
    ADD COLUMN IF NOT EXISTS harness_snapshot_hash text;

ALTER TABLE agent_run_handoff
    ADD COLUMN IF NOT EXISTS composition_edge_id text REFERENCES agent_composition_edge(edge_id),
    ADD COLUMN IF NOT EXISTS budget_grant_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS grant_hash text;

-- Backfill one Harness contract per current Profile. Contract JSON is canonical
-- jsonb text, so the same migration produces the same hash on every replay.
INSERT INTO agent_harness_contract(
    harness_contract_id,agent_key,profile_version,contract_version,agent_layer,runtime_kind,
    invocation_mode,default_deny,max_child_depth,lifecycle_policy_json,failure_policy_json,
    memory_policy_json,side_effect_policy_json,contract_json,contract_hash,status,published_at)
SELECT 'harness-'||d.agent_key||'-'||d.current_version,
       d.agent_key,d.current_version,'1.0.0',d.agent_layer,d.runtime_kind,
       'task',true,CASE WHEN d.agent_layer='application' THEN 1 ELSE 0 END,
       '{"allowPause":true,"allowResume":true,"allowCancel":true,"allowHumanTakeover":true}'::jsonb,
       '{"retryable":["timeout","provider_unavailable"],"escalateAfterAttempts":2}'::jsonb,
       '{"workingMemory":"run","crossRun":false,"retentionDays":30}'::jsonb,
       '{"default":"deny","allowExternalWrite":false,"requireIdempotencyKey":true}'::jsonb,
       jsonb_build_object('schemaVersion','harness.v1','agentKey',d.agent_key,'agentLayer',d.agent_layer,
                          'runtimeKind',d.runtime_kind,'defaultDeny',true,
                          'maxChildDepth',CASE WHEN d.agent_layer='application' THEN 1 ELSE 0 END),
       encode(sha256(convert_to((jsonb_build_object('schemaVersion','harness.v1','agentKey',d.agent_key,
                          'agentLayer',d.agent_layer,'runtimeKind',d.runtime_kind,'defaultDeny',true,
                          'maxChildDepth',CASE WHEN d.agent_layer='application' THEN 1 ELSE 0 END))::text,'UTF8')),'hex'),
       CASE WHEN d.status='published' THEN 'published' ELSE 'testing' END,
       CASE WHEN d.status='published' THEN now() ELSE NULL END
FROM agent_definition d
ON CONFLICT (agent_key,profile_version,contract_version) DO NOTHING;

UPDATE agent_definition d
SET agent_layer=COALESCE(d.agent_layer,'application'),
    runtime_kind=COALESCE(d.runtime_kind,'llm_loop');

-- Tool grants follow the existing Profile bindings. Profile-level approval and
-- registry risk are retained; no new write authority is silently introduced.
INSERT INTO agent_harness_tool_grant(harness_contract_id,tool_key,tool_version,permission,approval_required,grant_config_json)
SELECT c.harness_contract_id,b.tool_key,b.tool_version,
       CASE WHEN COALESCE(t.access_mode,'read_only')='write' THEN 'write' ELSE 'invoke' END,
       (COALESCE(b.approval_required,0)<>0 OR COALESCE(t.access_mode,'read_only')<>'read_only'
        OR COALESCE(t.risk_level,'medium') IN ('high','critical')),
       COALESCE(b.config_json,'{}'::jsonb)
FROM agent_harness_contract c
JOIN agent_tool_binding b ON b.agent_key=c.agent_key AND b.enabled<>0
JOIN tool_registry t ON t.tool_key=b.tool_key
ON CONFLICT (harness_contract_id,tool_key,tool_version) DO NOTHING;

-- Existing source policies become read-only data grants. They remain bounded by
-- the current Profile and do not create write permissions.
INSERT INTO agent_harness_data_grant(grant_id,harness_contract_id,resource_type,resource_pattern,access_mode,sensitivity_level)
SELECT 'data-grant-'||c.harness_contract_id||'-ontology',c.harness_contract_id,'source',
       COALESCE(m.config_json::jsonb->>'ontologySourceKey','uav-ontology'),'read','public'
FROM agent_harness_contract c JOIN agent_model_policy m ON m.agent_key=c.agent_key
ON CONFLICT (harness_contract_id,resource_type,resource_pattern,access_mode) DO NOTHING;

-- Source-policy domains are copied into explicit network grants when present.
INSERT INTO agent_harness_network_grant(network_grant_id,harness_contract_id,domain_pattern,methods_json)
SELECT 'net-grant-'||c.harness_contract_id||'-'||md5(domain),c.harness_contract_id,domain,'["GET"]'::jsonb
FROM agent_harness_contract c
JOIN agent_model_policy m ON m.agent_key=c.agent_key
CROSS JOIN LATERAL jsonb_array_elements_text(
    COALESCE(m.config_json::jsonb #> '{sourcePolicy,allowedDomains}','[]'::jsonb)) domains(domain)
WHERE length(domain)>0
ON CONFLICT (harness_contract_id,domain_pattern) DO NOTHING;

-- Every configured step gets a hard ceiling. Operators can tighten it per node
-- without changing the Agent-level budget.
INSERT INTO agent_harness_step_budget(harness_contract_id,step_key,model_calls,tool_calls,http_calls,search_calls,scrape_calls,input_tokens,output_tokens,max_parallelism,max_runtime_seconds)
SELECT c.harness_contract_id,s.step_key,
       COALESCE((m.config_json::jsonb #>> '{budgetLimits,model_calls}')::bigint,cp.max_model_calls),
       COALESCE((m.config_json::jsonb #>> '{budgetLimits,tool_calls}')::bigint,cp.max_tool_calls),
       COALESCE((m.config_json::jsonb #>> '{budgetLimits,http_calls}')::bigint,0),
       COALESCE((m.config_json::jsonb #>> '{budgetLimits,search_calls}')::bigint,0),
       COALESCE((m.config_json::jsonb #>> '{budgetLimits,scrape_calls}')::bigint,0),
       COALESCE((m.config_json::jsonb #>> '{budgetLimits,input_tokens}')::bigint,0),
       COALESCE((m.config_json::jsonb #>> '{budgetLimits,output_tokens}')::bigint,0),
       GREATEST(COALESCE(r.max_parallel_tools,1),1),
       GREATEST(COALESCE(cp.max_runtime_seconds,300),1)
FROM agent_harness_contract c
JOIN agent_step s ON s.agent_key=c.agent_key
LEFT JOIN agent_model_policy m ON m.agent_key=c.agent_key
LEFT JOIN agent_convergence_policy cp ON cp.agent_key=c.agent_key
LEFT JOIN agent_resource_policy r ON r.agent_key=c.agent_key
ON CONFLICT (harness_contract_id,step_key) DO NOTHING;

-- Declarative composition for the future-battlefield Profile. The existing
-- UAV graph remains available to legacy orchestration until its edges migrate.
INSERT INTO agent_composition_edge(edge_id,parent_agent_key,child_agent_key,parent_profile_version,child_profile_version,allowed_artifact_types_json,budget_mode,max_depth)
SELECT 'edge-future-battlefield-'||d.agent_key,'future-battlefield-analyst',d.agent_key,
       parent.current_version,d.current_version,'["agent-output","analysis-artifact","evidence-bundle"]'::jsonb,'shared',1
FROM agent_definition d
JOIN agent_definition parent ON parent.agent_key='future-battlefield-analyst'
WHERE d.agent_key IN ('evidence-collection-action','evidence-extraction-action','trend-analysis-action','scenario-analysis-action','red-team-action')
ON CONFLICT (parent_agent_key,child_agent_key,parent_profile_version) DO NOTHING;

INSERT INTO agent_composition_edge(edge_id,parent_agent_key,child_agent_key,parent_profile_version,child_profile_version,allowed_artifact_types_json,budget_mode,max_depth)
SELECT 'edge-uav-'||parent.agent_key||'-'||child.agent_key,parent.agent_key,child.agent_key,
       parent.current_version,child.current_version,'["agent-output"]'::jsonb,'shared',1
FROM (VALUES ('uav-capability-analyst','uav-design-engineer'),
             ('uav-capability-analyst','uav-evidence-reviewer'),
             ('uav-design-engineer','uav-evidence-reviewer')) v(parent_key,child_key)
JOIN agent_definition parent ON parent.agent_key=v.parent_key
JOIN agent_definition child ON child.agent_key=v.child_key
ON CONFLICT (parent_agent_key,child_agent_key,parent_profile_version) DO NOTHING;

-- Bind the current contract to the current Agent definition. It is nullable for
-- compatibility with historical snapshots and becomes mandatory for new Runs
-- in the application layer after this migration is installed.
ALTER TABLE agent_definition
    ADD COLUMN IF NOT EXISTS default_harness_contract_id text;

UPDATE agent_definition d
SET default_harness_contract_id=c.harness_contract_id
FROM agent_harness_contract c
WHERE c.agent_key=d.agent_key AND c.profile_version=d.current_version;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='agent_definition_harness_fkey') THEN
        ALTER TABLE agent_definition ADD CONSTRAINT agent_definition_harness_fkey
            FOREIGN KEY (default_harness_contract_id) REFERENCES agent_harness_contract(harness_contract_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='agent_run_harness_fkey') THEN
        ALTER TABLE agent_run ADD CONSTRAINT agent_run_harness_fkey
            FOREIGN KEY (harness_contract_id) REFERENCES agent_harness_contract(harness_contract_id);
    END IF;
END $$;

-- Append-only protection for authority and runtime snapshots.
DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'agent_harness_contract','agent_harness_tool_grant','agent_harness_data_grant',
        'agent_harness_network_grant','agent_composition_edge','agent_harness_step_budget',
        'agent_run_harness_snapshot'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I_immutable ON %I',table_name,table_name);
        EXECUTE format('CREATE TRIGGER %I_immutable BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation()',table_name,table_name);
    END LOOP;
END $$;

DROP TRIGGER IF EXISTS agent_harness_snapshot_fence ON agent_run_harness_snapshot;
CREATE TRIGGER agent_harness_snapshot_fence BEFORE INSERT OR UPDATE ON agent_run_harness_snapshot
FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard();

CREATE INDEX IF NOT EXISTS agent_harness_contract_agent_idx
    ON agent_harness_contract(agent_key,status);
CREATE INDEX IF NOT EXISTS agent_harness_edge_parent_idx
    ON agent_composition_edge(parent_agent_key,enabled);
CREATE INDEX IF NOT EXISTS agent_run_harness_snapshot_contract_idx
    ON agent_run_harness_snapshot(harness_contract_id,created_at);
