-- Verification requirement identifiers are scoped to a Run and lifecycle
-- revision. The same requirement label may legitimately be revised at a later
-- immutable gate without colliding with another research Run.
DO $$
DECLARE pk_name text;
BEGIN
    SELECT constraint_name INTO pk_name
      FROM information_schema.table_constraints
     WHERE table_schema=current_schema()
       AND table_name='future_battlefield_verification_requirement'
       AND constraint_type='PRIMARY KEY';
    IF pk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE future_battlefield_verification_requirement DROP CONSTRAINT %I', pk_name);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
         WHERE table_schema=current_schema()
           AND table_name='future_battlefield_verification_requirement'
           AND constraint_type='PRIMARY KEY') THEN
        ALTER TABLE future_battlefield_verification_requirement
            ADD PRIMARY KEY (run_id,lifecycle_revision,requirement_id);
    END IF;
END;
$$;
