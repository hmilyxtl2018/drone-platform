-- Effective usage is read from the existing reservation/settlement ledger.
-- Every dispatch serializes on this scope; there is no second usage counter.
CREATE TABLE IF NOT EXISTS agent_evolution_budget (
    cycle_id text PRIMARY KEY REFERENCES agent_evolution_cycle(cycle_id),
    limits_json jsonb NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
DROP TRIGGER IF EXISTS evolution_budget_immutable ON agent_evolution_budget;
CREATE TRIGGER evolution_budget_immutable BEFORE UPDATE OR DELETE ON agent_evolution_budget
FOR EACH ROW EXECUTE FUNCTION agent_research_history_immutable();
DROP TRIGGER IF EXISTS evolution_budget_no_truncate ON agent_evolution_budget;
CREATE TRIGGER evolution_budget_no_truncate BEFORE TRUNCATE ON agent_evolution_budget
FOR EACH STATEMENT EXECUTE FUNCTION agent_research_history_immutable();
