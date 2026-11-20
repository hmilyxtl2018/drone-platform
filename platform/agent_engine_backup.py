"""Back up the standalone admin DB and verify recovery in a unique new DB.

Credentials travel in a child process environment, never command arguments or
reports. Active token rows are deliberately excluded; issue fresh tokens after
disaster recovery. Provider credentials remain in the existing local key store.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(r"C:\Program Files\PostgreSQL\17\bin")
EXCLUDED = {"agent_admin_token"}


def _config(path=None):
    config = json.loads(Path(path or ROOT / "db.local.json").read_text(encoding="utf-8"))
    config["dbname"] = "ontology_agent_admin"
    return config


def _tool(name, config, database, *arguments):
    environment = os.environ.copy()
    environment["PGPASSWORD"] = config.get("password", "")
    command = [str(BIN / (name + ".exe")), "-h", str(config.get("host", "127.0.0.1")),
               "-p", str(config.get("port", 5432)), "-U", config["user"], "-d", database, *arguments]
    result = subprocess.run(command, env=environment, capture_output=True)
    if result.returncode:
        # stderr may contain SQL data. Retain a machine code only.
        raise RuntimeError(name + "-failed")


def manifest(config, database, *, connection=None):
    """Hash every persistent table without returning its rows or credentials."""
    owns = connection is None
    con = connection or psycopg2.connect(**{**config, "dbname": database})
    try:
        with con.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            names = [r["tablename"] for r in cursor.fetchall() if r["tablename"] not in EXCLUDED]
            tables = {}
            for name in names:
                cursor.execute(sql.SQL("SELECT to_jsonb(t)::text AS value FROM public.{} t ORDER BY to_jsonb(t)::text").format(sql.Identifier(name)))
                digest, count = hashlib.sha256(), 0
                for row in cursor:
                    digest.update(row["value"].encode("utf-8")); digest.update(b"\n"); count += 1
                tables[name] = {"rows": count, "sha256": digest.hexdigest()}
            return tables
    finally:
        if owns:
            con.close()


def backup(*, config_path=None, destination=None):
    config = _config(config_path)
    folder = Path(destination or ROOT / ".local" / "backups").resolve()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    archive = folder / ("ontology-agent-admin-" + stamp + ".dump")
    # Keep a single exported snapshot alive while hashing and pg_dump run, so
    # concurrent worker heartbeats cannot produce a false recovery mismatch.
    with closing(psycopg2.connect(**config)) as con:
        con.set_session(isolation_level="REPEATABLE READ", readonly=True)
        with con.cursor() as cursor:
            cursor.execute("SELECT pg_export_snapshot()")
            snapshot = cursor.fetchone()[0]
        with con.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT token_id,principal_id,created_at,expires_at FROM agent_admin_token ORDER BY token_id")
            token_metadata = [dict(row) for row in cursor.fetchall()]
        tables = manifest(config, config["dbname"], connection=con)
        _tool("pg_dump", config, config["dbname"], "--format=custom", "--no-owner", "--no-acl",
              "--snapshot=" + snapshot, "--exclude-table-data=public.agent_admin_token", "--file=" + str(archive))
    report = {"database": config["dbname"], "archive": str(archive), "createdAt": datetime.now(timezone.utc).isoformat(),
              "archiveSha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "tables": tables,
              "excludedTableData": sorted(EXCLUDED), "credentials": "server-side-only; active sessions excluded"}
    report["tokenMetadata"] = token_metadata
    sidecar = archive.with_suffix(".manifest.json")
    sidecar.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report


def verify(archive_path, *, config_path=None):
    config = _config(config_path)
    archive = Path(archive_path).resolve()
    expected = json.loads(archive.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected["archiveSha256"]:
        raise ValueError("backup-archive-hash-mismatch")
    database = "agent_engine_verify_restore_" + secrets.token_hex(8)
    report = {"database": database, "sourceDatabase": "ontology_agent_admin", "archiveSha256": expected["archiveSha256"],
              "passed": False, "paidCalls": 0}
    created = False
    try:
        with closing(psycopg2.connect(**{**config, "dbname": "postgres"})) as admin:
            admin.autocommit = True
            with admin.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
                created = True
        _tool("pg_restore", config, database, "--exit-on-error", "--no-owner", "--no-acl", "--section=pre-data", "--section=data", str(archive))
        # Preserve audit token handles and their foreign keys, while replacing
        # every authentication digest with a revoked noncredential placeholder.
        with closing(psycopg2.connect(**{**config, "dbname": database})) as con:
            with con.cursor() as cursor:
                for row in expected.get("tokenMetadata", []):
                    digest = hashlib.sha256(("revoked-backup:" + row["token_id"]).encode()).hexdigest()
                    cursor.execute("INSERT INTO agent_admin_token(token_hash,token_id,principal_id,created_at,expires_at,revoked_at) VALUES(%s,%s,%s,%s,%s,clock_timestamp())",
                                   (digest, row["token_id"], row["principal_id"], row["created_at"], row["expires_at"]))
            con.commit()
        _tool("pg_restore", config, database, "--exit-on-error", "--no-owner", "--no-acl", "--section=post-data", str(archive))
        actual = manifest(config, database)
        report["tables"] = actual
        report["differences"] = sorted(k for k in set(expected["tables"]) | set(actual) if expected["tables"].get(k) != actual.get(k))
        with closing(psycopg2.connect(**{**config, "dbname": database})) as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM agent_admin_token WHERE revoked_at IS NULL AND expires_at>clock_timestamp()")
                report["restoredActiveTokenRows"] = cursor.fetchone()[0]
        categories = {"configuration": ["agent_definition", "agent_config_publication"],
                      "runs": ["agent_run", "agent_run_profile_snapshot"], "reviews": ["agent_human_review"],
                      "knowledge": ["agent_knowledge_change_set", "agent_knowledge_publication"],
                      "artifacts": ["agent_run_artifact"], "evaluation": ["agent_evaluation_run", "agent_evaluation_case_result"]}
        report["categories"] = {key: {name: actual.get(name) for name in names} for key, names in categories.items()}
        report["passed"] = not report["differences"] and report["restoredActiveTokenRows"] == 0 and all(name in actual for names in categories.values() for name in names)
    finally:
        if created:
            with closing(psycopg2.connect(**{**config, "dbname": "postgres"})) as admin:
                admin.autocommit = True
                with admin.cursor() as cursor:
                    # Only the exact random database created by this call.
                    cursor.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
            report["temporaryDatabaseDropped"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "verify"))
    parser.add_argument("--archive")
    parser.add_argument("--config")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.action == "verify" and not args.archive:
            parser.error("verify requires --archive")
        value = backup(config_path=args.config) if args.action == "backup" else verify(args.archive, config_path=args.config)
        if args.output:
            Path(args.output).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({key: val for key, val in value.items() if key not in {"tables", "tokenMetadata"}}, ensure_ascii=False, indent=2, default=str))
        if value.get("passed") is False:
            raise SystemExit(1)
    except (psycopg2.Error, OSError):
        raise SystemExit("backup-or-restore-failed; inspect local database availability") from None


if __name__ == "__main__":
    main()
