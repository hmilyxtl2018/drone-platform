"""Read-only legacy lookup and historical linkage; never dispatch legacy work."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3


class LegacyRunBridge:
    def __init__(self, repository, source_path=None):
        from agent_tools.ontology import DEFAULT_SOURCE
        self.repo, self.source = repository, Path(source_path or DEFAULT_SOURCE).resolve()

    def lookup(self, analysis_id):
        if not isinstance(analysis_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", analysis_id):
            raise ValueError("invalid AnalysisRun identity")
        with self.repo.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT * FROM agent_legacy_run_link WHERE analysis_run_id=%s", (analysis_id,))
                row = c.fetchone()
                return dict(row) if row else None

    def link(self, analysis_id, engine_run_id):
        self.lookup(analysis_id)
        con = sqlite3.connect(self.source.as_uri() + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only=ON")
            row = con.execute("SELECT * FROM ontology_analysis_run WHERE analysis_run_id=?", (analysis_id,)).fetchone()
            if not row:
                raise ValueError("legacy AnalysisRun not found")
            legacy = dict(row)
            if legacy["status"] not in {"completed", "completed-with-gaps", "requires-review", "failed", "cancelled"}:
                raise ValueError("only a terminal historical AnalysisRun can be associated")
            if con.execute("SELECT 1 FROM ontology_agent_task WHERE analysis_run_id=? AND status='running' LIMIT 1",
                           (analysis_id,)).fetchone():
                raise ValueError("legacy tasks still execute")
        finally:
            con.close()
        run = self.repo.get_run(engine_run_id)
        if not run or run["input_json"].get("entityId") != legacy["entity_id"]:
            raise ValueError("legacy and Engine entity must match")
        digest = hashlib.sha256(json.dumps(legacy, sort_keys=True, default=str).encode()).hexdigest()
        with self.repo.connection_factory() as db:
            with db.cursor() as c:
                c.execute("INSERT INTO agent_legacy_run_link(analysis_run_id,run_id,source_uri,legacy_status,snapshot_hash) "
                          "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(analysis_run_id) DO NOTHING",
                          (analysis_id, engine_run_id, self.source.as_uri(), legacy["status"], digest))
                c.execute("SELECT * FROM agent_legacy_run_link WHERE analysis_run_id=%s", (analysis_id,))
                result = dict(c.fetchone())
                if result["run_id"] != engine_run_id:
                    raise ValueError("AnalysisRun already associated with another Engine Run")
                return result
