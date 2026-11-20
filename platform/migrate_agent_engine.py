"""Apply reviewed, repeatable Agent Engine migrations to the admin database.

Database credentials are read by the existing connection factory. Errors expose
only the SQLSTATE and migration filename, never connection details or SQL data.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

from agent_engine.postgres import connection_factory


ROOT = Path(__file__).resolve().parents[1]


def migrate(paths, *, database="ontology_agent_admin", repeats=1):
    if database != "ontology_agent_admin" and not database.startswith("agent_engine_verify_"):
        raise ValueError("migration target must be the admin database or a verification database")
    factory = connection_factory(database=database)
    reports = []
    for value in paths:
        path = (ROOT / value).resolve()
        if path.parent != ROOT / "platform" / "migrations" or not path.name.endswith("_postgres.sql"):
            raise ValueError("select a reviewed PostgreSQL migration in platform/migrations")
        source = path.read_text(encoding="utf-8")
        for iteration in range(repeats):
            try:
                with factory() as con:
                    with con.cursor() as cursor:
                        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended('agent-engine-migration',0))")
                        cursor.execute(source)
            except psycopg2.Error as exc:
                raise RuntimeError(f"{path.name}: migration failed (SQLSTATE {exc.pgcode})") from None
            reports.append({"migration": path.name, "iteration": iteration + 1, "passed": True})
    return {"database": database, "checkedAt": datetime.now(timezone.utc).isoformat(), "results": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("migrations", nargs="+")
    parser.add_argument("--database", default="ontology_agent_admin")
    parser.add_argument("--repeat", type=int, choices=(1, 2), default=1)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = migrate(args.migrations, database=args.database, repeats=args.repeat)
    if args.output:
        path = ROOT / args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
