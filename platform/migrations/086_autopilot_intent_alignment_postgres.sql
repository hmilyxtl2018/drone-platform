-- Understanding is deliberately separate from permission to execute.
CREATE TABLE IF NOT EXISTS agent_autopilot_alignment_request (
 request_id text PRIMARY KEY,
 principal_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
 workspace_key text NOT NULL CHECK(workspace_key='ontology'),
 client_session_id text NOT NULL CHECK(length(client_session_id) BETWEEN 1 AND 200),
 statement text NOT NULL CHECK(length(btrim(statement)) BETWEEN 1 AND 10000),
 statement_hash text NOT NULL CHECK(length(statement_hash)=64),
 expected_revision bigint NOT NULL CHECK(expected_revision>=0),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 200),
 request_hash text NOT NULL CHECK(length(request_hash)=64),
 created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL DEFAULT (now()+interval '30 minutes'),
 UNIQUE(principal_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS agent_autopilot_alignment_revision (
 revision_id text PRIMARY KEY,
 principal_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
 workspace_key text NOT NULL CHECK(workspace_key='ontology'),
 revision bigint NOT NULL CHECK(revision>0),
 expected_revision bigint NOT NULL CHECK(expected_revision>=0),
 previous_revision_id text REFERENCES agent_autopilot_alignment_revision(revision_id),
 source_request_id text NOT NULL REFERENCES agent_autopilot_alignment_request(request_id),
 action text NOT NULL CHECK(action IN ('model-proposal','save-draft','confirm-understanding')),
 actor_kind text NOT NULL CHECK(actor_kind IN ('dsh-model','human')),
 document_json jsonb NOT NULL CHECK(jsonb_typeof(document_json)='object'),
 content_hash text NOT NULL CHECK(length(content_hash)=64),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 200),
 request_hash text NOT NULL CHECK(length(request_hash)=64),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(principal_id,workspace_key,revision),
 UNIQUE(principal_id,idempotency_key),
 CHECK((action='model-proposal')=(actor_kind='dsh-model'))
);
CREATE UNIQUE INDEX IF NOT EXISTS agent_autopilot_alignment_proposal_once
 ON agent_autopilot_alignment_revision(source_request_id) WHERE action='model-proposal';
CREATE INDEX IF NOT EXISTS agent_autopilot_alignment_latest
 ON agent_autopilot_alignment_revision(principal_id,workspace_key,revision DESC);

CREATE OR REPLACE FUNCTION agent_autopilot_alignment_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'intent alignment records are append-only' USING ERRCODE='55000'; END; $$;
CREATE OR REPLACE FUNCTION agent_autopilot_alignment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE current_revision bigint; previous_id text; request_owner text;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('alignment:'||NEW.principal_id,0));
 SELECT revision,revision_id INTO current_revision,previous_id FROM agent_autopilot_alignment_revision
 WHERE principal_id=NEW.principal_id AND workspace_key=NEW.workspace_key ORDER BY revision DESC LIMIT 1;
 IF NEW.expected_revision<>COALESCE(current_revision,0) OR NEW.revision<>COALESCE(current_revision,0)+1
 OR NEW.previous_revision_id IS DISTINCT FROM previous_id THEN
  RAISE EXCEPTION 'alignment revision conflict' USING ERRCODE='40001';
 END IF;
 SELECT principal_id INTO request_owner FROM agent_autopilot_alignment_request WHERE request_id=NEW.source_request_id;
 IF request_owner IS DISTINCT FROM NEW.principal_id THEN
  RAISE EXCEPTION 'alignment request ownership mismatch' USING ERRCODE='42501';
 END IF;
 IF NEW.actor_kind='human' AND NOT EXISTS(SELECT 1 FROM agent_admin_identity
 WHERE principal_id=NEW.principal_id AND enabled AND kind='human' AND roles_json ?| ARRAY['operator','admin']) THEN
  RAISE EXCEPTION 'human operator required' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END; $$;
DO $$ DECLARE target text; BEGIN
 FOREACH target IN ARRAY ARRAY['agent_autopilot_alignment_request','agent_autopilot_alignment_revision'] LOOP
  EXECUTE format('DROP TRIGGER IF EXISTS alignment_immutable ON %I',target);
  EXECUTE format('CREATE TRIGGER alignment_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION agent_autopilot_alignment_immutable()',target);
  EXECUTE format('DROP TRIGGER IF EXISTS alignment_no_truncate ON %I',target);
  EXECUTE format('CREATE TRIGGER alignment_no_truncate BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION agent_autopilot_alignment_immutable()',target);
 END LOOP;
END $$;
DROP TRIGGER IF EXISTS alignment_guard ON agent_autopilot_alignment_revision;
CREATE TRIGGER alignment_guard BEFORE INSERT ON agent_autopilot_alignment_revision
 FOR EACH ROW EXECUTE FUNCTION agent_autopilot_alignment_guard();
