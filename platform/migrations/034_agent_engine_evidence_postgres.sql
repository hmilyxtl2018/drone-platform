-- Phase 3 evidence persistence. Apply after 032; does not alter live ontology.
-- Review decisions belong in a separate append-only review module. No update
-- API in this migration can promote a source/candidate to trusted or approved.

CREATE TABLE IF NOT EXISTS agent_source_snapshot (
    source_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    artifact_id text NOT NULL REFERENCES agent_run_artifact(artifact_id),
    artifact_hash text NOT NULL CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    source_uri text NOT NULL,
    content_kind text NOT NULL CHECK (content_kind IN ('markdown','ontology-quote')),
    locator_json jsonb NOT NULL CHECK (jsonb_typeof(locator_json)='object'),
    metadata_json jsonb NOT NULL CHECK (jsonb_typeof(metadata_json)='object'),
    verification_status text NOT NULL DEFAULT 'unverified' CHECK (verification_status='unverified'),
    review_status text NOT NULL DEFAULT 'pending' CHECK (review_status='pending'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(run_id,artifact_id,locator_json),
    UNIQUE(run_id,source_id)
);
CREATE INDEX IF NOT EXISTS agent_source_snapshot_run_idx ON agent_source_snapshot(run_id,created_at);

CREATE TABLE IF NOT EXISTS agent_evidence_candidate (
    candidate_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    source_id text NOT NULL,
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    entity_id text NOT NULL,
    metric text NOT NULL,
    candidate_json jsonb NOT NULL CHECK (jsonb_typeof(candidate_json)='object'),
    quote text NOT NULL CHECK (length(quote)>0),
    start_offset integer NOT NULL CHECK (start_offset>=0),
    end_offset integer NOT NULL CHECK (end_offset>start_offset),
    offset_unit text NOT NULL DEFAULT 'unicode-codepoint' CHECK (offset_unit='unicode-codepoint'),
    citation_validated boolean NOT NULL CHECK (citation_validated=true),
    truth_verified boolean NOT NULL DEFAULT false CHECK (truth_verified=false),
    verification_status text NOT NULL DEFAULT 'unverified' CHECK (verification_status='unverified'),
    review_status text NOT NULL DEFAULT 'pending' CHECK (review_status='pending'),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(run_id,source_id) REFERENCES agent_source_snapshot(run_id,source_id),
    UNIQUE(run_id,fingerprint)
);
CREATE INDEX IF NOT EXISTS agent_evidence_candidate_run_idx ON agent_evidence_candidate(run_id,entity_id,metric);

CREATE OR REPLACE FUNCTION agent_evidence_reference_integrity() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE artifact record; snapshot record; body text; expected_path jsonb;
BEGIN
    IF TG_TABLE_NAME='agent_source_snapshot' THEN
        SELECT run_id,content_hash,content_json INTO artifact FROM agent_run_artifact WHERE artifact_id=NEW.artifact_id;
        IF artifact.run_id IS DISTINCT FROM NEW.run_id OR artifact.content_hash IS DISTINCT FROM NEW.artifact_hash THEN
            RAISE EXCEPTION 'source artifact ownership/hash mismatch' USING ERRCODE='23514';
        END IF;
        IF NEW.content_kind='markdown' THEN
            expected_path='{"path":["markdown"],"offsetUnit":"unicode-codepoint"}'::jsonb;
            body=artifact.content_json->>'markdown';
        ELSE
            IF jsonb_typeof(NEW.locator_json->'evidenceIndex')<>'number'
               OR (NEW.locator_json->>'evidenceIndex') !~ '^[0-9]+$' THEN
                RAISE EXCEPTION 'ontology excerpt index invalid' USING ERRCODE='23514';
            END IF;
            expected_path=jsonb_build_object('path',jsonb_build_array('sourceEvidence',(NEW.locator_json->>'evidenceIndex')::int,'quote'),
                'evidenceIndex',(NEW.locator_json->>'evidenceIndex')::int,'offsetUnit','unicode-codepoint');
            body=artifact.content_json->'sourceEvidence'->((NEW.locator_json->>'evidenceIndex')::int)->>'quote';
        END IF;
        IF NEW.locator_json<>expected_path OR body IS NULL
           OR encode(sha256(convert_to(body,'UTF8')),'hex')<>NEW.content_hash THEN
            RAISE EXCEPTION 'source body hash/locator mismatch' USING ERRCODE='23514';
        END IF;
    ELSE
        SELECT s.*,a.content_json INTO snapshot FROM agent_source_snapshot s
            JOIN agent_run_artifact a ON a.artifact_id=s.artifact_id
            WHERE s.source_id=NEW.source_id AND s.run_id=NEW.run_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'candidate source missing or foreign' USING ERRCODE='23514'; END IF;
        IF snapshot.content_kind='markdown' THEN body=snapshot.content_json->>'markdown';
        ELSE body=snapshot.content_json->'sourceEvidence'->((snapshot.locator_json->>'evidenceIndex')::int)->>'quote'; END IF;
        IF NEW.end_offset>length(body) OR substring(body from NEW.start_offset+1 for NEW.end_offset-NEW.start_offset)<>NEW.quote
           OR NEW.candidate_json->>'sourceArtifactId' IS DISTINCT FROM snapshot.artifact_id
           OR NEW.candidate_json->>'sourceArtifactHash' IS DISTINCT FROM snapshot.artifact_hash
           OR NEW.candidate_json->>'contentHash' IS DISTINCT FROM snapshot.content_hash
           OR NEW.candidate_json->>'sourceId' IS DISTINCT FROM NEW.source_id
           OR NEW.candidate_json->>'entityId' IS DISTINCT FROM NEW.entity_id
           OR NEW.candidate_json->>'metric' IS DISTINCT FROM NEW.metric
           OR NEW.candidate_json->>'fingerprint' IS DISTINCT FROM NEW.fingerprint
           OR NEW.candidate_json->>'quote' IS DISTINCT FROM NEW.quote
           OR NEW.candidate_json->>'startOffset' IS DISTINCT FROM NEW.start_offset::text
           OR NEW.candidate_json->>'endOffset' IS DISTINCT FROM NEW.end_offset::text
           OR NEW.candidate_json->>'reviewStatus' IS DISTINCT FROM 'pending'
           OR NEW.candidate_json->>'verificationStatus' IS DISTINCT FROM 'unverified'
           OR NEW.candidate_json->>'truthVerified' IS DISTINCT FROM 'false'
           OR NEW.candidate_json->>'citationValidated' IS DISTINCT FROM 'true' THEN
            RAISE EXCEPTION 'candidate citation or immutable identity mismatch' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS agent_source_reference_check ON agent_source_snapshot;
CREATE TRIGGER agent_source_reference_check BEFORE INSERT ON agent_source_snapshot
FOR EACH ROW EXECUTE FUNCTION agent_evidence_reference_integrity();
DROP TRIGGER IF EXISTS agent_candidate_reference_check ON agent_evidence_candidate;
CREATE TRIGGER agent_candidate_reference_check BEFORE INSERT ON agent_evidence_candidate
FOR EACH ROW EXECUTE FUNCTION agent_evidence_reference_integrity();
DROP TRIGGER IF EXISTS agent_source_snapshot_immutable ON agent_source_snapshot;
CREATE TRIGGER agent_source_snapshot_immutable BEFORE UPDATE OR DELETE ON agent_source_snapshot
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_evidence_candidate_immutable ON agent_evidence_candidate;
CREATE TRIGGER agent_evidence_candidate_immutable BEFORE UPDATE OR DELETE ON agent_evidence_candidate
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
