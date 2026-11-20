-- Human opinions on exact immutable report versions; no approval/publication grant.
CREATE TABLE IF NOT EXISTS agent_report_review_snapshot (
 snapshot_id text PRIMARY KEY,
 run_id text NOT NULL REFERENCES agent_run(run_id),
 report_hash text NOT NULL CHECK(length(report_hash)=64),
 source_graph_hash text CHECK(source_graph_hash IS NULL OR length(source_graph_hash)=64),
 manifest_json jsonb NOT NULL CHECK(jsonb_typeof(manifest_json)='object'),
 content_hash text NOT NULL CHECK(length(content_hash)=64),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS agent_report_review_snapshot_version_idx
 ON agent_report_review_snapshot(run_id,report_hash,COALESCE(source_graph_hash,''));

CREATE TABLE IF NOT EXISTS agent_report_review_event (
 event_id text PRIMARY KEY,
 snapshot_id text NOT NULL REFERENCES agent_report_review_snapshot(snapshot_id),
 object_kind text NOT NULL CHECK(object_kind IN ('report','node','edge','evidence')),
 object_id text NOT NULL CHECK(length(object_id) BETWEEN 1 AND 4096),
 action text NOT NULL CHECK(action IN ('comment','objection','request-evidence','scope-confirmation','correction')),
 reviewer_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
 reviewer_display_name text NOT NULL,
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 8000),
 evidence_ids_json jsonb NOT NULL CHECK(jsonb_typeof(evidence_ids_json)='array'),
 revision bigint NOT NULL CHECK(revision>0),
 expected_revision bigint NOT NULL CHECK(expected_revision>=0),
 previous_event_id text REFERENCES agent_report_review_event(event_id),
 supersedes_event_id text REFERENCES agent_report_review_event(event_id),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 200),
 request_hash text NOT NULL CHECK(length(request_hash)=64),
 content_hash text NOT NULL CHECK(length(content_hash)=64),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(snapshot_id,object_kind,object_id,revision),
 UNIQUE(reviewer_id,idempotency_key),
 CHECK((action='correction')=(supersedes_event_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS agent_report_review_event_object_idx
 ON agent_report_review_event(snapshot_id,object_kind,object_id,revision);
CREATE UNIQUE INDEX IF NOT EXISTS agent_report_review_event_supersedes_idx
 ON agent_report_review_event(supersedes_event_id) WHERE supersedes_event_id IS NOT NULL;

CREATE OR REPLACE FUNCTION agent_report_review_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'report review records are append-only' USING ERRCODE='55000';
END; $$;

CREATE OR REPLACE FUNCTION agent_report_review_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE manifest jsonb; current_revision bigint; preceding text; old_event record;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM agent_admin_identity WHERE principal_id=NEW.reviewer_id
   AND enabled=true AND kind='human' AND (roles_json ?| ARRAY['reviewer','admin'])) THEN
  RAISE EXCEPTION 'human reviewer or administrator required' USING ERRCODE='42501';
 END IF;
 SELECT manifest_json INTO manifest FROM agent_report_review_snapshot WHERE snapshot_id=NEW.snapshot_id;
 IF manifest IS NULL OR NOT(COALESCE(manifest->'objects'->NEW.object_kind,'[]'::jsonb) ? NEW.object_id) THEN
  RAISE EXCEPTION 'object is outside the frozen report manifest' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(NEW.evidence_ids_json) AS v(id)
   WHERE NOT(COALESCE(manifest->'objects'->'evidence','[]'::jsonb) ? v.id)) THEN
  RAISE EXCEPTION 'evidence is outside the frozen report manifest' USING ERRCODE='23514';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('report-review-object:'||NEW.snapshot_id||':'||NEW.object_kind||':'||NEW.object_id,0));
 SELECT revision,event_id INTO current_revision,preceding FROM agent_report_review_event
   WHERE snapshot_id=NEW.snapshot_id AND object_kind=NEW.object_kind AND object_id=NEW.object_id ORDER BY revision DESC LIMIT 1;
 current_revision=COALESCE(current_revision,0);
 IF NEW.expected_revision<>current_revision OR NEW.revision<>current_revision+1 OR NEW.previous_event_id IS DISTINCT FROM preceding THEN
  RAISE EXCEPTION 'report review object revision conflict' USING ERRCODE='40001';
 END IF;
 IF NEW.supersedes_event_id IS NOT NULL THEN
  SELECT * INTO old_event FROM agent_report_review_event WHERE event_id=NEW.supersedes_event_id;
  IF old_event.event_id IS NULL OR old_event.snapshot_id<>NEW.snapshot_id OR old_event.object_kind<>NEW.object_kind
    OR old_event.object_id<>NEW.object_id OR old_event.reviewer_id<>NEW.reviewer_id THEN
   RAISE EXCEPTION 'correction must refer to own event on the same exact object and version' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END; $$;

DO $$ DECLARE target text; BEGIN
 FOREACH target IN ARRAY ARRAY['agent_report_review_snapshot','agent_report_review_event'] LOOP
  EXECUTE format('DROP TRIGGER IF EXISTS report_review_immutable ON %I',target);
  EXECUTE format('CREATE TRIGGER report_review_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION agent_report_review_immutable()',target);
  EXECUTE format('DROP TRIGGER IF EXISTS report_review_no_truncate ON %I',target);
  EXECUTE format('CREATE TRIGGER report_review_no_truncate BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION agent_report_review_immutable()',target);
 END LOOP;
END $$;
DROP TRIGGER IF EXISTS report_review_insert_guard ON agent_report_review_event;
CREATE TRIGGER report_review_insert_guard BEFORE INSERT ON agent_report_review_event
 FOR EACH ROW EXECUTE FUNCTION agent_report_review_insert_guard();
