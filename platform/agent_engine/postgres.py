from __future__ import annotations
import json
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor

class ClosingConnection(psycopg2.extensions.connection):
    """Commit/rollback and close: psycopg2's default context leaves it open."""
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()

def connection_factory(config_path: str | Path | None = None, database: str = 'ontology_agent_admin'):
    path = Path(config_path or Path(__file__).resolve().parents[2] / 'db.local.json')
    config = json.loads(path.read_text(encoding='utf-8'))
    config['dbname'] = database
    def connect():
        return psycopg2.connect(connection_factory=ClosingConnection, cursor_factory=RealDictCursor, **config)
    return connect
