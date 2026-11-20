-- Snapshot-bound human review. Apply after 035 and 034; preserve all history.
ALTER TABLE agent_human_review ADD COLUMN IF NOT EXISTS run_version text;
ALTER TABLE agent_human_review ADD COLUMN IF NOT EXISTS profile_hash text CHECK(profile_hash ~ '^[0-9a-f]{64}$');
ALTER TABLE agent_human_review ADD COLUMN IF NOT EXISTS evidence_set_hash text CHECK(evidence_set_hash ~ '^[0-9a-f]{64}$');

CREATE OR REPLACE FUNCTION agent_review_evidence_hash(target_run text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT encode(sha256(convert_to(COALESCE(string_agg(item,E'\n' ORDER BY item),''),'UTF8')),'hex') FROM (
        SELECT jsonb_build_array('source',source_id,artifact_hash,content_hash)::text AS item
            FROM agent_source_snapshot WHERE run_id=target_run
        UNION ALL SELECT jsonb_build_array('candidate',candidate_id,fingerprint)::text
            FROM agent_evidence_candidate WHERE run_id=target_run
        UNION ALL SELECT jsonb_build_array('artifact',artifact_id,content_hash)::text
            FROM agent_run_artifact WHERE run_id=target_run
    ) evidence;
$$;

CREATE OR REPLACE FUNCTION agent_review_snapshot_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE version_value text; profile_value text; latest_id text;
BEGIN
    SELECT agent_version INTO version_value FROM agent_run WHERE run_id=NEW.run_id FOR UPDATE;
    SELECT profile_hash INTO profile_value FROM agent_run_profile_snapshot WHERE run_id=NEW.run_id;
    IF NEW.run_version IS NULL OR NEW.profile_hash IS NULL OR NEW.evidence_set_hash IS NULL OR profile_value IS NULL
       OR NEW.run_version IS DISTINCT FROM version_value OR NEW.profile_hash IS DISTINCT FROM profile_value
       OR NEW.evidence_set_hash IS DISTINCT FROM agent_review_evidence_hash(NEW.run_id) THEN
        RAISE EXCEPTION 'review run/profile/evidence snapshot changed or missing' USING ERRCODE='23514';
    END IF;
    SELECT review_id INTO latest_id FROM agent_human_review WHERE run_id=NEW.run_id
        AND subject_type=NEW.subject_type AND subject_id=NEW.subject_id ORDER BY review_seq DESC LIMIT 1;
    IF NEW.supersedes_review_id IS DISTINCT FROM latest_id THEN
        RAISE EXCEPTION 'review must append after the current subject decision' USING ERRCODE='23514';
    END IF;
    IF NEW.decision='resolve-conflict' AND (
       NEW.subject_type<>'candidate' OR NEW.conflict_id IS NULL
       OR NEW.conflict_id !~ '^conflict-[0-9a-f]{64}$') THEN
        RAISE EXCEPTION 'conflict review requires exact candidate conflict identity' USING ERRCODE='23514';
    ELSIF NEW.decision<>'resolve-conflict' AND NEW.conflict_id IS NOT NULL THEN
        RAISE EXCEPTION 'conflict identity only applies to conflict resolution' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS agent_review_snapshot_integrity ON agent_human_review;
CREATE TRIGGER agent_review_snapshot_integrity BEFORE INSERT ON agent_human_review
FOR EACH ROW EXECUTE FUNCTION agent_review_snapshot_integrity();

-- The caller supplies trusted control_context for human decisions. Source and
-- candidate writes remain protected by the same worker generation/lease fence.
DO $$
DECLARE table_name text;
BEGIN
    IF to_regprocedure('agent_worker_fence_guard()') IS NOT NULL THEN
        FOREACH table_name IN ARRAY ARRAY['agent_source_snapshot','agent_evidence_candidate'] LOOP
            EXECUTE format('DROP TRIGGER IF EXISTS agent_worker_fence ON %I',table_name);
            EXECUTE format('CREATE TRIGGER agent_worker_fence BEFORE INSERT OR UPDATE ON %I '
                           'FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard()',table_name);
        END LOOP;
    END IF;
END;
$$;
