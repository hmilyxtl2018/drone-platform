-- Ontology Reasoning Harness: additive, repeatable, append-only run records.
-- Prerequisites: 033 worker fencing, 034 evidence, 035 identities, 059 evidence refs.
-- No existing Run, source ontology, evidence row or artifact is rewritten.

CREATE TABLE IF NOT EXISTS agent_reasoning_policy (
    policy_id text PRIMARY KEY,
    agent_key text NOT NULL REFERENCES agent_definition(agent_key),
    profile_version text NOT NULL,
    version integer NOT NULL CHECK (version>0),
    policy_json jsonb NOT NULL CHECK (jsonb_typeof(policy_json)='object'),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(agent_key,version)
);
CREATE TABLE IF NOT EXISTS agent_reasoning_stage (
    policy_id text NOT NULL REFERENCES agent_reasoning_policy(policy_id),
    stage_key text NOT NULL,
    sequence_no integer NOT NULL CHECK (sequence_no>0),
    stage_json jsonb NOT NULL CHECK (jsonb_typeof(stage_json)='object'),
    PRIMARY KEY(policy_id,stage_key),
    UNIQUE(policy_id,sequence_no)
);
CREATE TABLE IF NOT EXISTS agent_run_reasoning_snapshot (
    run_id text PRIMARY KEY REFERENCES agent_run(run_id),
    policy_id text,
    policy_version integer NOT NULL CHECK(policy_version>0),
    policy_json jsonb NOT NULL CHECK (jsonb_typeof(policy_json)='object'),
    policy_hash text NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_reasoning_stage_usage (
    usage_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run_reasoning_snapshot(run_id),
    stage_key text NOT NULL,
    iteration integer NOT NULL CHECK(iteration>0),
    operation_key text NOT NULL,
    charges_json jsonb NOT NULL CHECK(jsonb_typeof(charges_json)='object'),
    content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(run_id,operation_key)
);
CREATE INDEX IF NOT EXISTS agent_reasoning_stage_usage_run_idx
    ON agent_reasoning_stage_usage(run_id,stage_key,iteration);
CREATE TABLE IF NOT EXISTS agent_reasoning_checkpoint (
    checkpoint_id text PRIMARY KEY,
    checkpoint_seq bigserial UNIQUE,
    run_id text NOT NULL REFERENCES agent_run_reasoning_snapshot(run_id),
    stage_key text NOT NULL,
    step_run_id text REFERENCES agent_step_run(step_run_id),
    operation_key text NOT NULL,
    checkpoint_json jsonb NOT NULL CHECK(jsonb_typeof(checkpoint_json)='object'),
    checkpoint_hash text NOT NULL CHECK(checkpoint_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(run_id,operation_key),
    UNIQUE(run_id,checkpoint_id)
);
CREATE INDEX IF NOT EXISTS agent_reasoning_checkpoint_run_idx
    ON agent_reasoning_checkpoint(run_id,checkpoint_seq);
CREATE TABLE IF NOT EXISTS agent_reasoning_gate_result (
    result_id text PRIMARY KEY,
    result_seq bigserial UNIQUE,
    run_id text NOT NULL REFERENCES agent_run_reasoning_snapshot(run_id),
    checkpoint_id text NOT NULL,
    gate_key text NOT NULL,
    operation_key text NOT NULL,
    status text NOT NULL CHECK(status IN ('passed','failed','warning','skipped','unavailable','not-applicable')),
    blocking boolean NOT NULL DEFAULT false,
    result_json jsonb NOT NULL CHECK(jsonb_typeof(result_json)='object'),
    result_hash text NOT NULL CHECK(result_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(run_id,checkpoint_id) REFERENCES agent_reasoning_checkpoint(run_id,checkpoint_id),
    UNIQUE(run_id,operation_key)
);
CREATE INDEX IF NOT EXISTS agent_reasoning_gate_run_idx ON agent_reasoning_gate_result(run_id,result_seq);
CREATE TABLE IF NOT EXISTS agent_relation_candidate (
    candidate_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run_reasoning_snapshot(run_id),
    checkpoint_id text NOT NULL,
    relation_key text NOT NULL,
    candidate_json jsonb NOT NULL CHECK(jsonb_typeof(candidate_json)='object'),
    content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
    truth_status text NOT NULL DEFAULT 'unverified' CHECK(truth_status='unverified'),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(run_id,checkpoint_id) REFERENCES agent_reasoning_checkpoint(run_id,checkpoint_id),
    UNIQUE(run_id,candidate_id),
    UNIQUE(run_id,relation_key,content_hash)
);
CREATE INDEX IF NOT EXISTS agent_relation_candidate_run_idx ON agent_relation_candidate(run_id,created_at,candidate_id);
CREATE TABLE IF NOT EXISTS agent_relation_candidate_event (
    event_id text PRIMARY KEY,
    event_seq bigserial UNIQUE,
    run_id text NOT NULL REFERENCES agent_run_reasoning_snapshot(run_id),
    candidate_id text NOT NULL,
    status text NOT NULL CHECK(status IN ('proposed','evidence-bound','support-reviewed','conflicted',
        'needs-more-evidence','accepted-as-unverified','rejected','published','withdrawn')),
    action text NOT NULL,
    actor_id text NOT NULL,
    operation_key text NOT NULL,
    event_json jsonb NOT NULL CHECK(jsonb_typeof(event_json)='object'),
    content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(run_id,candidate_id) REFERENCES agent_relation_candidate(run_id,candidate_id),
    UNIQUE(run_id,operation_key)
);
CREATE INDEX IF NOT EXISTS agent_relation_event_run_idx ON agent_relation_candidate_event(run_id,candidate_id,event_seq);
CREATE TABLE IF NOT EXISTS agent_relation_publication (
    publication_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run_reasoning_snapshot(run_id),
    candidate_id text NOT NULL,
    review_event_id text NOT NULL REFERENCES agent_relation_candidate_event(event_id),
    artifact_id text NOT NULL REFERENCES agent_run_artifact(artifact_id),
    publication_json jsonb NOT NULL CHECK(jsonb_typeof(publication_json)='object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(run_id,candidate_id) REFERENCES agent_relation_candidate(run_id,candidate_id),
    UNIQUE(run_id,candidate_id)
);

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
            IF NEW.action='accept' AND NOT EXISTS(SELECT 1 FROM agent_model_call c
                WHERE c.run_id=NEW.run_id AND c.status='succeeded'
                  AND c.call_id=(candidate->'supportReview'->>'reviewCallId')
                  AND c.output_artifact_id=(candidate->'supportReview'->>'reviewArtifactId')) THEN
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
                NOT EXISTS(SELECT 1 FROM agent_model_call c WHERE c.run_id=NEW.run_id AND c.status='succeeded'
                    AND c.call_id=(candidate->'supportReview'->>'reviewCallId')
                    AND c.output_artifact_id=(candidate->'supportReview'->>'reviewArtifactId'))
                OR NOT EXISTS(SELECT 1 FROM agent_model_call c WHERE c.run_id=NEW.run_id AND c.status='succeeded'
                    AND c.call_id=(candidate->'supportReview'->>'generatorCallId')
                    AND c.output_artifact_id=(candidate->'supportReview'->>'generatorArtifactId'))
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

DO $$ DECLARE table_name text; BEGIN
    FOREACH table_name IN ARRAY ARRAY['agent_reasoning_policy','agent_reasoning_stage',
        'agent_run_reasoning_snapshot','agent_reasoning_stage_usage','agent_reasoning_checkpoint',
        'agent_reasoning_gate_result','agent_relation_candidate','agent_relation_candidate_event','agent_relation_publication'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS reasoning_immutable ON %I',table_name);
        EXECUTE format('CREATE TRIGGER reasoning_immutable BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation()',table_name);
        IF table_name NOT IN ('agent_reasoning_policy','agent_reasoning_stage') THEN
            EXECUTE format('DROP TRIGGER IF EXISTS reasoning_worker_fence ON %I',table_name);
            EXECUTE format('CREATE TRIGGER reasoning_worker_fence BEFORE INSERT ON %I '
                'FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard()',table_name);
        END IF;
    END LOOP;
    FOREACH table_name IN ARRAY ARRAY['agent_reasoning_stage_usage','agent_reasoning_checkpoint',
        'agent_relation_candidate','agent_relation_candidate_event','agent_relation_publication'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS reasoning_integrity ON %I',table_name);
        EXECUTE format('CREATE TRIGGER reasoning_integrity BEFORE INSERT ON %I '
            'FOR EACH ROW EXECUTE FUNCTION agent_reasoning_record_guard()',table_name);
    END LOOP;
END $$;

-- Policy authoring hashes are computed in Python when loaded. The immutable Run
-- snapshot hash uses exactly the artifact hash canonicalization, avoiding a
-- misleading comparison between PostgreSQL jsonb::text and Python JSON ordering.
WITH stages AS (
    SELECT jsonb_agg(jsonb_build_object('key',v.step_key,'stepKey',v.step_key,
        'sequence',v.sequence,'name',v.name,'required',true,'maxRounds',4,
        'budget',jsonb_build_object('gate_checks',300,'relation_reviews',80),
        'qualityGates',v.gates) ORDER BY v.sequence) AS stages_json
    FROM (VALUES
        (1,'define-research-question','问题与本体范围','["question-bounded"]'::jsonb),
        (2,'generate-research-hypotheses','可检验假设','["conditions-present"]'::jsonb),
        (3,'plan-source-coverage','证据计划','["source-allowlist-valid"]'::jsonb),
        (4,'collect-public-evidence','公开来源研究','["source-allowlist-valid","evidence-hash-valid","quote-locator-valid"]'::jsonb),
        (5,'extract-facts','事实与引文抽取','["evidence-hash-valid","quote-locator-valid"]'::jsonb),
        (6,'extract-war-system','本体关系候选','["ontology-contract-valid","relation-endpoints-valid"]'::jsonb),
        (7,'fuse-evidence-and-conflicts','证据融合与冲突','["independent-source-checked","unknowns-present"]'::jsonb),
        (8,'analyze-by-perspective','条件化多视角研判','["relation-evidence-bound","relation-support-reviewed","conditions-present","scenario-dimensions-complete"]'::jsonb),
        (9,'red-team-challenge','独立反证审查','["counterargument-present","unknowns-present"]'::jsonb),
        (10,'compose-judgment-report','图谱与专家报告','["ontology-contract-valid","relation-evidence-bound","relation-support-reviewed","unknowns-present","indicators-present"]'::jsonb),
        (11,'human-review','完成质量检查','["unknowns-present"]'::jsonb)
    ) AS v(sequence,step_key,name,gates)
)
INSERT INTO agent_reasoning_policy(policy_id,agent_key,profile_version,version,policy_json,created_by)
SELECT 'reasoning-future-battlefield-analyst-v1','future-battlefield-analyst',d.current_version,1,
    jsonb_build_object('policyId','reasoning-future-battlefield-analyst-v1',
        'agentKey','future-battlefield-analyst','profileVersion',d.current_version,'version',1,
        'mode','ontology-guided','stages',stages.stages_json,
        'qualityGates',(SELECT jsonb_agg(jsonb_build_object('gateId',gate,
            'stages',(SELECT jsonb_agg(s->>'key') FROM jsonb_array_elements(stages.stages_json) s WHERE s->'qualityGates' ? gate)))
            FROM (SELECT DISTINCT jsonb_array_elements_text(s->'qualityGates') AS gate FROM jsonb_array_elements(stages.stages_json) s) g),
        'budgetLimits','{"reasoning_rounds":4,"gate_checks":300,"relation_reviews":80}'::jsonb,
        'requireEvidenceBinding',true,'allowAutomaticPublication',false,
        'ontology','{"entityTypes":["system","capability","technology","constraint","context","outcome"],"predicates":["depends-on","constrains","enables","changes","part-of","associated-with"]}'::jsonb,
        'ontologyContract','{"entityTypes":["system","capability","technology","constraint","context","outcome"],"predicates":["depends-on","constrains","enables","changes","part-of","associated-with"]}'::jsonb),
    'migration-070'
FROM agent_definition d CROSS JOIN stages WHERE d.agent_key='future-battlefield-analyst'
ON CONFLICT(agent_key,version) DO NOTHING;
INSERT INTO agent_reasoning_stage(policy_id,stage_key,sequence_no,stage_json)
SELECT p.policy_id,s->>'key',(s->>'sequence')::integer,s FROM agent_reasoning_policy p
CROSS JOIN LATERAL jsonb_array_elements(p.policy_json->'stages') s
WHERE p.policy_id='reasoning-future-battlefield-analyst-v1'
ON CONFLICT(policy_id,stage_key) DO NOTHING;
