-- A support proof may belong to the report Run or its completed, hash-bound
-- perspective Action. Both calls must be successful, correctly staged and owned
-- by the SAME Run; no sibling, uncompleted, or unrelated Run is accepted.
CREATE OR REPLACE FUNCTION agent_relation_support_proof(report_run text,candidate jsonb)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(
  SELECT 1 FROM agent_model_call review
  JOIN agent_model_call generator ON generator.run_id=review.run_id
  JOIN agent_run_artifact review_output ON review_output.artifact_id=review.output_artifact_id
    AND review_output.run_id=review.run_id AND review_output.artifact_type='model-output'
  JOIN agent_run_artifact generator_output ON generator_output.artifact_id=generator.output_artifact_id
    AND generator_output.run_id=generator.run_id AND generator_output.artifact_type='model-output'
  WHERE review.call_id=candidate->'supportReview'->>'reviewCallId'
    AND generator.call_id=candidate->'supportReview'->>'generatorCallId'
    AND review.output_artifact_id=candidate->'supportReview'->>'reviewArtifactId'
    AND generator.output_artifact_id=candidate->'supportReview'->>'generatorArtifactId'
    AND review.status='succeeded' AND generator.status='succeeded'
    AND review.stage='future-battlefield-support-review' AND generator.stage='future-battlefield-perspectives'
    AND (review.run_id=report_run OR EXISTS(
     SELECT 1 FROM agent_action_invocation a
     JOIN agent_run_artifact output ON output.artifact_id=a.output_artifact_id AND output.run_id=a.child_run_id
     WHERE a.run_id=report_run AND a.child_run_id=review.run_id
       AND a.parent_step_key='analyze-by-perspective'
       AND a.status IN ('completed','completed-with-gaps','requires-human-review')
       AND output.content_hash=a.output_artifact_hash))
 );
$$;

-- Retain every existing lifecycle, human-review and evidence guard; only the
-- provenance predicate now recognizes a verified delegation edge.
CREATE OR REPLACE FUNCTION agent_reasoning_record_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE item jsonb; bound_evidence_id text; snapshot jsonb; candidate jsonb; prior_status text; actor record;
BEGIN
    SELECT policy_json INTO snapshot FROM agent_run_reasoning_snapshot WHERE run_id=NEW.run_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'reasoning snapshot absent' USING ERRCODE='23514'; END IF;
    IF TG_TABLE_NAME IN ('agent_reasoning_checkpoint','agent_reasoning_stage_usage') THEN
        IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'stages') s WHERE s->>'key'=NEW.stage_key) THEN
            RAISE EXCEPTION 'reasoning stage outside frozen policy' USING ERRCODE='23514';
        END IF;
        IF TG_TABLE_NAME='agent_reasoning_checkpoint' THEN
            IF NEW.step_run_id IS NOT NULL THEN
                IF NOT EXISTS(SELECT 1 FROM agent_step_run WHERE step_run_id=NEW.step_run_id AND run_id=NEW.run_id) THEN
                    RAISE EXCEPTION 'reasoning step ownership mismatch' USING ERRCODE='23514';
                END IF;
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME='agent_relation_candidate' THEN
        IF NEW.candidate_json->>'truthStatus' IS DISTINCT FROM 'unverified' THEN
            RAISE EXCEPTION 'relation candidate cannot assert verified truth' USING ERRCODE='23514';
        END IF;
        IF jsonb_typeof(NEW.candidate_json->'evidenceIds') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'relation evidenceIds must be an array' USING ERRCODE='23514';
        END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(NEW.candidate_json->'evidenceIds') LOOP
            bound_evidence_id := item#>>'{}';
            IF NOT EXISTS(SELECT 1 FROM future_battlefield_evidence_ref f WHERE f.run_id=NEW.run_id AND f.evidence_id=bound_evidence_id)
               AND NOT EXISTS(SELECT 1 FROM agent_evidence_candidate e WHERE e.run_id=NEW.run_id AND e.candidate_id=bound_evidence_id) THEN
                RAISE EXCEPTION 'relation evidence absent or belongs to different run' USING ERRCODE='23514';
            END IF;
        END LOOP;
    ELSIF TG_TABLE_NAME='agent_relation_candidate_event' THEN
        SELECT candidate_json INTO candidate FROM agent_relation_candidate
            WHERE run_id=NEW.run_id AND candidate_id=NEW.candidate_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'relation candidate absent' USING ERRCODE='23514'; END IF;
        SELECT status INTO prior_status FROM agent_relation_candidate_event
            WHERE run_id=NEW.run_id AND candidate_id=NEW.candidate_id ORDER BY event_seq DESC LIMIT 1;
        IF NEW.action IN ('accept','reject','request-evidence','mark-conflict','withdraw','publish') THEN
            SELECT * INTO actor FROM agent_admin_identity WHERE principal_id=NEW.actor_id FOR SHARE;
            IF NOT FOUND OR NOT actor.enabled OR actor.kind<>'human'
               OR NOT (actor.roles_json ? 'reviewer' OR actor.roles_json ? 'admin') THEN
                RAISE EXCEPTION 'authenticated human relation reviewer required' USING ERRCODE='42501';
            END IF;
            IF NEW.action='accept' AND (prior_status IN ('conflicted','withdrawn','published')
                OR candidate->'supportReview'->>'verdict' IS DISTINCT FROM 'supported'
                OR candidate->'supportReview'->>'method' IS NULL
                OR candidate->'supportReview'->>'reviewCallId' IS NULL
                OR candidate->'supportReview'->>'reviewArtifactId' IS NULL
                OR candidate->'supportReview'->>'generatorCallId' IS NULL
                OR candidate->'supportReview'->>'generatorArtifactId' IS NULL
                OR candidate->'supportReview'->'evidenceIds' IS DISTINCT FROM candidate->'evidenceIds'
                OR jsonb_array_length(candidate->'evidenceIds')=0) THEN
                RAISE EXCEPTION 'candidate lacks supported evidence or has unresolved conflict' USING ERRCODE='23514';
            END IF;
            IF NEW.action='accept' AND NOT EXISTS(SELECT 1 FROM agent_reasoning_gate_result g
                JOIN agent_reasoning_checkpoint cp ON cp.run_id=g.run_id AND cp.checkpoint_id=g.checkpoint_id
                WHERE g.run_id=NEW.run_id AND cp.checkpoint_id=(SELECT checkpoint_id FROM agent_relation_candidate
                    WHERE run_id=NEW.run_id AND candidate_id=NEW.candidate_id)
                  AND g.gate_key='relation-support-reviewed'
                  AND g.status='passed') THEN
                RAISE EXCEPTION 'candidate has no persisted passing support gate' USING ERRCODE='23514';
            END IF;
            IF NEW.action='accept' AND NOT agent_relation_support_proof(NEW.run_id,candidate) THEN
                RAISE EXCEPTION 'candidate support review call proof is absent' USING ERRCODE='23514';
            END IF;
            IF NEW.action='publish' AND (prior_status IS DISTINCT FROM 'accepted-as-unverified'
                OR EXISTS(SELECT 1 FROM agent_reasoning_gate_result WHERE run_id=NEW.run_id AND blocking
                          AND status NOT IN ('passed','skipped','not-applicable'))) THEN
                RAISE EXCEPTION 'candidate publication blocked by acceptance or quality gates' USING ERRCODE='23514';
            END IF;
        END IF;
        IF NEW.action='initialize' THEN
            IF NOT ((prior_status IS NULL AND NEW.status='proposed')
                OR (prior_status='proposed' AND NEW.status IN ('evidence-bound','needs-more-evidence','conflicted'))
                OR (prior_status='evidence-bound' AND NEW.status IN ('support-reviewed','needs-more-evidence','conflicted'))) THEN
                RAISE EXCEPTION 'invalid initial relation lifecycle transition' USING ERRCODE='23514';
            END IF;
            IF NEW.actor_id<>'reasoning-harness' THEN
                RAISE EXCEPTION 'invalid initial relation actor' USING ERRCODE='23514';
            END IF;
            IF NEW.status='support-reviewed' AND (candidate->'supportReview'->>'verdict' IS DISTINCT FROM 'supported'
                OR candidate->'supportReview'->>'method' IS NULL
                OR candidate->'supportReview'->>'reviewCallId' IS NULL
                OR candidate->'supportReview'->>'reviewArtifactId' IS NULL
                OR candidate->'supportReview'->>'generatorCallId' IS NULL
                OR candidate->'supportReview'->>'generatorArtifactId' IS NULL
                OR candidate->'supportReview'->'evidenceIds' IS DISTINCT FROM candidate->'evidenceIds') THEN
                RAISE EXCEPTION 'support review evidence differs from relation evidence' USING ERRCODE='23514';
            END IF;
            IF NEW.status='support-reviewed' AND (
                NOT agent_relation_support_proof(NEW.run_id,candidate)
            ) THEN
                RAISE EXCEPTION 'support review provenance calls are absent' USING ERRCODE='23514';
            END IF;
        ELSIF NEW.action IN ('accept','reject','request-evidence','mark-conflict','withdraw','publish') THEN
            IF NEW.status IS DISTINCT FROM (CASE NEW.action
                WHEN 'accept' THEN 'accepted-as-unverified' WHEN 'reject' THEN 'rejected'
                WHEN 'request-evidence' THEN 'needs-more-evidence' WHEN 'mark-conflict' THEN 'conflicted'
                WHEN 'withdraw' THEN 'withdrawn' WHEN 'publish' THEN 'published' END) THEN
                RAISE EXCEPTION 'relation review action/status mismatch' USING ERRCODE='23514';
            END IF;
            IF prior_status='published' AND NEW.action<>'withdraw' THEN
                RAISE EXCEPTION 'published relation can only be withdrawn' USING ERRCODE='23514';
            END IF;
            IF prior_status IN ('withdrawn','rejected') AND NEW.action<>'request-evidence' THEN
                RAISE EXCEPTION 'terminal relation requires new evidence' USING ERRCODE='23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'unknown relation lifecycle action' USING ERRCODE='23514';
        END IF;
        IF NEW.action='accept' AND prior_status IS DISTINCT FROM 'support-reviewed' THEN
            RAISE EXCEPTION 'candidate support review is not current' USING ERRCODE='23514';
        END IF;
        IF NEW.action='publish' AND prior_status IS DISTINCT FROM 'accepted-as-unverified' THEN
            RAISE EXCEPTION 'candidate has no current human acceptance' USING ERRCODE='23514';
        END IF;
        IF NEW.action='mark-conflict' AND prior_status IN ('withdrawn','rejected','published') THEN
            RAISE EXCEPTION 'candidate lifecycle does not permit conflict transition' USING ERRCODE='23514';
        END IF;
        IF NEW.action='withdraw' AND prior_status NOT IN ('published','accepted-as-unverified','support-reviewed','needs-more-evidence','conflicted','evidence-bound','proposed') THEN
            RAISE EXCEPTION 'candidate lifecycle does not permit withdrawal' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='agent_relation_publication' THEN
        IF NOT EXISTS(SELECT 1 FROM agent_run_artifact a WHERE a.artifact_id=NEW.artifact_id AND a.run_id=NEW.run_id)
           OR NOT EXISTS(SELECT 1 FROM agent_relation_candidate_event e WHERE e.event_id=NEW.review_event_id
                 AND e.run_id=NEW.run_id AND e.candidate_id=NEW.candidate_id AND e.action='publish' AND e.status='published') THEN
            RAISE EXCEPTION 'publication ownership or human review mismatch' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
