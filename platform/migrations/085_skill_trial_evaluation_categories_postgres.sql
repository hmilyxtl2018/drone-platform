-- Unlabelled is explicit; it must never masquerade as an expert-classified case.
ALTER TABLE agent_evaluation_case
    DROP CONSTRAINT IF EXISTS agent_evaluation_case_case_type_check;
ALTER TABLE agent_evaluation_case ADD CONSTRAINT agent_evaluation_case_case_type_check
    CHECK (case_type IN ('sufficient_data','missing_data','conflicting_data','wrong_units',
        'unauthorized_action','sufficient','missing','conflicting','wrong-units','unauthorized',
        'unlabelled','out_of_scope'));
