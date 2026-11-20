-- DSH is an external conversational surface. These tables bind a DSH turn to
-- an immutable Agent Run; Run, evidence and ontology records remain canonical.
CREATE TABLE IF NOT EXISTS agent_dsh_exposure_policy (
    agent_key text PRIMARY KEY REFERENCES agent_definition(agent_key),
    enabled boolean NOT NULL DEFAULT false,
    allowed_modes_json jsonb NOT NULL DEFAULT '["unattended-draft"]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dsh_session (
    session_id text PRIMARY KEY,
    principal_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    tenant_id text NOT NULL DEFAULT 'local',
    agent_profile text NOT NULL REFERENCES agent_definition(agent_key),
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dsh_turn (
    turn_id text PRIMARY KEY,
    session_id text NOT NULL REFERENCES dsh_session(session_id),
    run_id text REFERENCES agent_run(run_id),
    idempotency_key text,
    question text NOT NULL,
    input_json jsonb NOT NULL,
    status text NOT NULL CHECK(status IN ('creating','queued','running','completed','failed')),
    answer_json jsonb,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(session_id,idempotency_key)
);

CREATE TABLE IF NOT EXISTS dsh_event_cursor (
    turn_id text PRIMARY KEY REFERENCES dsh_turn(turn_id),
    event_id bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dsh_session_principal_idx ON dsh_session(principal_id,updated_at DESC);
CREATE INDEX IF NOT EXISTS dsh_turn_session_idx ON dsh_turn(session_id,created_at DESC);
CREATE INDEX IF NOT EXISTS dsh_turn_run_idx ON dsh_turn(run_id) WHERE run_id IS NOT NULL;

INSERT INTO agent_dsh_exposure_policy(agent_key,enabled,allowed_modes_json)
SELECT agent_key,true,'["unattended-draft","expert-draft"]'::jsonb
  FROM agent_definition
 WHERE agent_key='future-battlefield-codex'
ON CONFLICT(agent_key) DO UPDATE SET enabled=EXCLUDED.enabled,allowed_modes_json=EXCLUDED.allowed_modes_json,updated_at=now();

INSERT INTO agent_dsh_exposure_policy(agent_key,enabled,allowed_modes_json)
SELECT agent_key,true,'["unattended-draft"]'::jsonb
  FROM agent_definition
 WHERE agent_key='future-battlefield-analyst'
ON CONFLICT(agent_key) DO UPDATE SET enabled=EXCLUDED.enabled,allowed_modes_json=EXCLUDED.allowed_modes_json,updated_at=now();
