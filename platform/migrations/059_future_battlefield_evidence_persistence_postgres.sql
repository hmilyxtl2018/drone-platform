-- P0-C: durable Claim/Evidence/Artifact references for Future Battlefield.
-- A JSON citation is only display data until this row exists.

CREATE TABLE IF NOT EXISTS future_battlefield_evidence_ref (
    evidence_id text NOT NULL,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    source_artifact_id text NOT NULL REFERENCES agent_run_artifact(artifact_id),
    source_artifact_hash text NOT NULL CHECK (source_artifact_hash ~ '^[0-9a-f]{64}$'),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    quote text NOT NULL CHECK (length(quote)>0),
    source_uri text NOT NULL,
    locator_json jsonb NOT NULL CHECK (jsonb_typeof(locator_json)='object'),
    claim_id text,
    truth_status text NOT NULL CHECK (truth_status IN ('unverified','human-reviewed-unverified','verified')),
    start_offset integer,
    end_offset integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id,evidence_id)
);
CREATE INDEX IF NOT EXISTS future_battlefield_evidence_artifact_idx
    ON future_battlefield_evidence_ref(run_id,source_artifact_id,created_at);

CREATE OR REPLACE FUNCTION future_battlefield_evidence_integrity() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE artifact record; claim_json jsonb; body text;
BEGIN
    SELECT run_id,content_hash,content_json INTO artifact FROM agent_run_artifact
     WHERE artifact_id=NEW.source_artifact_id;
    IF NOT FOUND OR artifact.run_id IS DISTINCT FROM NEW.run_id
       OR artifact.content_hash IS DISTINCT FROM NEW.source_artifact_hash THEN
        RAISE EXCEPTION 'Future evidence source artifact ownership/hash mismatch' USING ERRCODE='23514';
    END IF;
    IF NEW.locator_json IS NULL OR jsonb_typeof(NEW.locator_json)<>'object' THEN
        RAISE EXCEPTION 'Future evidence locator must be an object' USING ERRCODE='23514';
    END IF;
    SELECT value INTO claim_json
      FROM jsonb_array_elements(COALESCE(artifact.content_json->'claims','[]'::jsonb))
     WHERE value->>'claimId'=COALESCE(NEW.claim_id,NEW.evidence_id)
     LIMIT 1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Future evidence claim is absent from source artifact' USING ERRCODE='23514';
    END IF;
    body := claim_json->>'body';
    IF claim_json->>'sourceUri' IS DISTINCT FROM NEW.source_uri
       OR body IS NULL
       OR encode(sha256(convert_to(body,'UTF8')),'hex') IS DISTINCT FROM NEW.content_hash
       OR (NEW.start_offset IS NOT NULL AND
           substring(body from NEW.start_offset+1 for NEW.end_offset-NEW.start_offset) IS DISTINCT FROM NEW.quote) THEN
        RAISE EXCEPTION 'Future evidence quote/source/hash mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS future_battlefield_evidence_integrity ON future_battlefield_evidence_ref;
CREATE TRIGGER future_battlefield_evidence_integrity BEFORE INSERT ON future_battlefield_evidence_ref
FOR EACH ROW EXECUTE FUNCTION future_battlefield_evidence_integrity();
DROP TRIGGER IF EXISTS future_battlefield_evidence_fence ON future_battlefield_evidence_ref;
CREATE TRIGGER future_battlefield_evidence_fence BEFORE INSERT ON future_battlefield_evidence_ref
FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard();
DROP TRIGGER IF EXISTS future_battlefield_evidence_immutable ON future_battlefield_evidence_ref;
CREATE TRIGGER future_battlefield_evidence_immutable BEFORE UPDATE OR DELETE ON future_battlefield_evidence_ref
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();

INSERT INTO agent_validation_rule(rule_id,agent_key,rule_type,expression,severity)
VALUES ('future-evidence-persistence','future-battlefield-analyst','evidence-persistence',
        'every cited Future Battlefield evidence object must resolve to an immutable Run artifact reference','blocking')
ON CONFLICT (rule_id) DO NOTHING;

UPDATE agent_output_contract
SET schema_json=jsonb_set(
    schema_json::jsonb,
    '{properties,findings,items,properties,supportingEvidence,items,required}',
    '["evidenceId","sourceArtifactId","sourceArtifactHash","contentHash","locator","truthStatus"]'::jsonb,
    true
 )::text
WHERE agent_key='future-battlefield-analyst'
  AND schema_json::jsonb ? 'properties';
