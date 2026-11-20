"""Export the ontology workbench's SQLite data without changing source databases.

SQLite backup() includes committed WAL contents. Each database is a separate
snapshot: finish collection/extraction and stop writes before the final handoff
if a consistent snapshot across multiple databases is required.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import sqlite3
import sys

PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT / "ontology" / "coast-raid-k"
DATABASES = (
    ("coast-raid-k-intelligence.sqlite", "CRK_INTELLIGENCE_SQLITE_PATH", True),
    ("coast-raid-k-osint.sqlite", "CRK_OSINT_SQLITE_PATH", True),
    ("coast-raid-k-runtime.sqlite", "CRK_RUNTIME_SQLITE_PATH", True),
    ("ontology-agent-admin.sqlite", None, False),
)


def database_info(path: Path) -> dict:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as con:
        checks = [row[0] for row in con.execute("PRAGMA quick_check")]
        if checks != ["ok"]:
            raise RuntimeError(f"Integrity check failed for {path.name}: {checks}")
        tables = [row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        counts = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            counts[table] = con.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "bytes": path.stat().st_size,
        "sha256": digest,
        "integrity": "ok",
        "tableCounts": counts,
    }


def export(output: Path) -> dict:
    archive = output.with_name(output.name + ".zip")
    if output.exists() or archive.exists():
        raise FileExistsError("Choose a new output directory; existing exports are never overwritten")
    sources = []
    for name, variable, required in DATABASES:
        source = Path(os.environ.get(variable, "") or PACKAGE / name) if variable else PACKAGE / name
        source = source.expanduser().resolve()
        if not source.is_file():
            if required:
                raise FileNotFoundError(f"Required database is missing: {source}")
            continue
        sources.append((name, source))

    output.mkdir(parents=True)
    destination = output / "ontology" / "coast-raid-k"
    destination.mkdir(parents=True)
    manifest = {
        "schemaVersion": "uav-workbench-transfer.v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "scope": "Shared workbench SQLite stores; PostgreSQL and credentials are excluded",
        "snapshotConsistency": "Independent SQLite snapshots; quiesce writes for final handoff",
        "files": {},
    }
    for name, source in sources:
        target = destination / name
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(target)) as dst:
                src.backup(dst, pages=512, sleep=0.05)
        relative = target.relative_to(output).as_posix()
        manifest["files"][relative] = {"method": "sqlite-backup", **database_info(target)}

    # The static knowledge view can be rebuilt from the checked-in JSON package.
    # This avoids exporting a stale/damaged static DB or depending on PostgreSQL.
    # PostgreSQL-only content must be migrated separately if it is needed.
    sys.path.insert(0, str(PACKAGE))
    try:
        loader = runpy.run_path(str(PACKAGE / "load_db.py"))
        target = destination / "coast-raid-k.sqlite"
        loader["emit_sqlite"](PACKAGE, output_path=target)
    finally:
        sys.path.pop(0)
    relative = target.relative_to(output).as_posix()
    manifest["files"][relative] = {"method": "rebuilt-from-json", **database_info(target)}
    (output / "transfer-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    shutil.make_archive(str(output), "zip", root_dir=output)
    return {
        "directory": str(output),
        "archive": str(archive),
        "databaseCount": len(manifest["files"]),
        "entityCount": manifest["files"]["ontology/coast-raid-k/coast-raid-k-intelligence.sqlite"]
            ["tableCounts"].get("ontology_entity", 0),
        "manifest": str(output / "transfer-manifest.json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new export directory (a sibling ZIP is also created)")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = (args.output or PROJECT / ".local" / "transfers" / f"uav-data-{stamp}").expanduser().resolve()
    try:
        result = export(output)
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"Export failed: {exc}\nAn incomplete output directory must not be used for migration.\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
