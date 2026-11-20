-- Additive resource names; no historical budget or snapshot is rewritten.
CREATE OR REPLACE FUNCTION agent_phase3_valid_counters(value jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE item record;
BEGIN
    IF jsonb_typeof(value) <> 'object' THEN RETURN false; END IF;
    FOR item IN SELECT * FROM jsonb_each(value) LOOP
        IF item.key NOT IN ('model_calls','input_tokens','output_tokens','tool_calls',
                            'http_calls','search_calls','scrape_calls',
                            'reasoning_rounds','gate_checks','relation_reviews')
           OR jsonb_typeof(item.value) <> 'number'
           OR item.value::text !~ '^[0-9]+$'
           OR (item.value::text)::numeric > 9223372036854775807
        THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
END;
$$;
