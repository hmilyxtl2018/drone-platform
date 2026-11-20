-- P0-C provenance and temporal qualifiers.  These fields describe what was
-- observed and how a source relates to an origin; they never certify truth.

ALTER TABLE future_battlefield_evidence_ref
    ADD COLUMN IF NOT EXISTS published_at text,
    ADD COLUMN IF NOT EXISTS observed_at text,
    ADD COLUMN IF NOT EXISTS effective_at text,
    ADD COLUMN IF NOT EXISTS origin_source_uri text,
    ADD COLUMN IF NOT EXISTS source_relation text NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS independence_status text NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS start_offset integer,
    ADD COLUMN IF NOT EXISTS end_offset integer;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='future_evidence_source_relation_check') THEN
        ALTER TABLE future_battlefield_evidence_ref ADD CONSTRAINT future_evidence_source_relation_check
            CHECK (source_relation IN ('original','republished','aggregator','translation','unknown'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='future_evidence_independence_check') THEN
        ALTER TABLE future_battlefield_evidence_ref ADD CONSTRAINT future_evidence_independence_check
            CHECK (independence_status IN ('unknown','same-origin','independent-candidate','human-verified-independent'));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='future_evidence_offsets_check') THEN
        ALTER TABLE future_battlefield_evidence_ref ADD CONSTRAINT future_evidence_offsets_check
            CHECK (start_offset IS NULL AND end_offset IS NULL OR
                   start_offset >= 0 AND end_offset > start_offset);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS future_battlefield_evidence_temporal_idx
    ON future_battlefield_evidence_ref(run_id,observed_at,published_at);

INSERT INTO agent_validation_rule(rule_id,agent_key,rule_type,expression,severity)
VALUES ('future-source-provenance-temporal','future-battlefield-analyst','source-provenance-temporal',
        'source relation, origin identity and observed/published/effective time remain explicit; independence defaults to unknown','blocking')
ON CONFLICT (rule_id) DO NOTHING;
