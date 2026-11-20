"""Standalone SQLite store for Agent and tool administration."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT.parent / "ontology" / "coast-raid-k" / "ontology-agent-admin.sqlite"
SCHEMA = (ROOT / "migrations" / "025_agent_admin_standalone.sql").read_text(encoding="utf-8")
EXECUTION_SCHEMA = (ROOT / "migrations" / "026_agent_execution_spec.sql").read_text(encoding="utf-8")
PROFILE_POLICY_SCHEMA = (ROOT / "migrations" / "027_agent_profile_policies.sql").read_text(encoding="utf-8")
RUNTIME_SCHEMA = (ROOT / "migrations" / "028_agent_runtime.sql").read_text(encoding="utf-8")
SEED_STEPS_SCHEMA = (ROOT / "migrations" / "029_agent_seed_steps.sql").read_text(encoding="utf-8")

def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

class AgentAdminStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def initialize(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA)
            con.executescript(EXECUTION_SCHEMA)
            con.executescript(PROFILE_POLICY_SCHEMA)
            con.executescript(RUNTIME_SCHEMA)
            con.executescript(SEED_STEPS_SCHEMA)

    def counts(self) -> dict[str, int]:
        with self.connect() as con:
            return {name: con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                    for name in ("agent_definition", "tool_registry", "agent_tool_binding", "workflow_definition")}

if __name__ == "__main__":
    store = AgentAdminStore()
    print({"database": str(store.path), **store.counts()})
