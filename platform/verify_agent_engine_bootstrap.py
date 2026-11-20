"""Restore the admin bootstrap into a uniquely named temporary DB and verify POC.

Only the database created by this process is dropped. Credentials stay in the
local config/process environment, never in command arguments or reports.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import time
from contextlib import closing
from pathlib import Path

import psycopg2
from psycopg2 import sql

from agent_engine.bootstrap import build_engine
from agent_engine.postgres import connection_factory
from agent_tools.fixtures import X200_CAPABILITY_INPUT
from migrate_agent_engine import migrate


ROOT = Path(__file__).resolve().parents[1]
SEED_TABLES = (
    'agent_definition', 'agent_approval_policy', 'agent_convergence_policy',
    'agent_evidence_policy', 'agent_loop_rule', 'agent_model_policy',
    'agent_observability_policy', 'agent_output_contract', 'agent_resource_policy',
    'agent_scope', 'agent_step', 'tool_registry', 'agent_step_tool',
    'agent_tool_binding', 'agent_validation_rule', 'agent_version', 'tool_version',
    'workflow_definition',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-dump', action='store_true', help='regenerate schema and config seeds from the current admin DB')
    parser.add_argument('--output', default='docs/verification/agent-engine-bootstrap.json')
    args = parser.parse_args()
    config = json.loads((ROOT / 'db.local.json').read_text(encoding='utf-8'))
    env = os.environ.copy()
    env['PGPASSWORD'] = config.get('password', '')
    pg_bin = Path(r'C:\Program Files\PostgreSQL\17\bin')
    connect_args = ['-h', str(config.get('host', '127.0.0.1')), '-p', str(config.get('port', 5432)), '-U', config['user']]
    dump_path = ROOT / 'db/ontology-agent-admin-postgres.sql'

    def run_pg(name, arguments):
        result = subprocess.run([str(pg_bin / (name + '.exe')), *connect_args, *arguments],
                                env=env, capture_output=True)
        if result.returncode:
            raise RuntimeError(f'{name} failed with exit code {result.returncode}')
        return result.stdout

    if args.refresh_dump:
        schema = run_pg('pg_dump', ['-d', 'ontology_agent_admin', '--schema-only', '--no-owner', '--no-privileges'])
        flags = [flag for table in SEED_TABLES for flag in ('--table', 'public.' + table)]
        seeds = run_pg('pg_dump', ['-d', 'ontology_agent_admin', '--data-only', '--no-owner', '--no-privileges', *flags])
        dump_path.write_bytes(schema + b'\n' + seeds)
    database = 'agent_engine_verify_' + secrets.token_hex(8)
    admin_config = {**config, 'dbname': 'postgres'}
    created = False
    report = {'temporaryDatabase': database, 'bootstrapFile': str(dump_path), 'passed': False}
    try:
        with closing(psycopg2.connect(**admin_config)) as admin:
            admin.autocommit = True
            with admin.cursor() as cur:
                cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
                created = True
        run_pg('psql', ['-d', database, '--no-psqlrc', '--set', 'ON_ERROR_STOP=1', '-f', str(dump_path)])
        foundation_paths = sorted(
            p.relative_to(ROOT).as_posix()
            for p in (ROOT / 'platform/migrations').glob('*_postgres.sql')
            if p.name[:3].isdigit() and 30 <= int(p.name[:3]) <= 39
            and 'agent_engine' in p.name)
        migrate(foundation_paths, database=database, repeats=2)
        # The historical dump predates the admin publication migration. The
        # runtime only needs its additive DAG column for this bootstrap smoke
        # and the full admin migration is exercised by phase3 verification.
        with connection_factory(database=database)() as con:
            with con.cursor() as cur:
                cur.execute("ALTER TABLE agent_step ADD COLUMN IF NOT EXISTS dependencies_json jsonb NOT NULL DEFAULT '[]'::jsonb")
        # Keep the bootstrap dump focused on the seed schema; apply the P0
        # Harness contract migration explicitly so authority rows are rebuilt
        # from the restored Profile and tool bindings.
        harness_migration = migrate(
            ['platform/migrations/053_agent_harness_contract_postgres.sql'],
            database=database, repeats=2)
        baseline_migration = migrate(
            ['platform/migrations/054_agent_execution_baseline_postgres.sql'],
            database=database, repeats=2)
        context_migration = migrate(
            ['platform/migrations/055_agent_context_collaboration_postgres.sql',
             'platform/migrations/059_agent_handoff_projection_postgres.sql',
             'platform/migrations/060_agent_context_hash_chain_postgres.sql',
             'platform/migrations/065_uav_agent_execution_steps_postgres.sql'],
            database=database, repeats=2)
        from register_execution_baselines import register as register_baselines
        baselines = register_baselines(database)
        factory = connection_factory(database=database)
        with factory() as con:
            with con.cursor() as cur:
                counts = {}
                for table in ('agent_definition', 'tool_registry', 'agent_step', 'agent_run'):
                    cur.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table)))
                    counts[table] = cur.fetchone()['n']
                cur.execute("SELECT count(*) AS n FROM pg_trigger WHERE NOT tgisinternal AND tgname IN ('agent_run_event_immutable','agent_tool_call_immutable','agent_profile_snapshot_immutable','agent_artifact_immutable')")
                counts['immutableTriggers'] = cur.fetchone()['n']
        assert counts == {'agent_definition':3,'tool_registry':15,'agent_step':13,'agent_run':0,'immutableTriggers':4}, counts
        engine = build_engine(database=database)
        ids = [engine.create_run('uav-capability-analyst', X200_CAPABILITY_INPUT) for _ in range(2)]
        async def execute_pair():
            return await asyncio.gather(*(engine.execute(rid) for rid in ids))
        start = time.perf_counter()
        results = asyncio.run(execute_pair())
        reports = []
        for rid, result in zip(ids, results):
            contexts = engine.repo.list_contexts(rid)
            tool_calls = engine.repo.list_tool_calls(rid)
            context_by_id = {row['context_id']: row for row in contexts}
            # Every parallel tool invocation must retain its own step-run
            # binding; a shared mutable RunContext must never collapse them
            # onto the last completed branch.
            assert len(contexts) == 8 and len(context_by_id) == 8, contexts
            assert all(row.get('step_run_id') for row in contexts), contexts
            assert all(call.get('context_id') in context_by_id and
                       context_by_id[call['context_id']]['step_run_id'] == call.get('step_run_id')
                       for call in tool_calls), (contexts, tool_calls)
            assert all(row.get('chain_hash') for row in contexts), contexts
            item = {'runId': rid, 'status':result['status'], 'steps':len(engine.repo.list_steps(rid)),
                    'toolCalls':len(tool_calls), 'contexts':len(contexts),
                    'toolContextBindings':len({call.get('context_id') for call in tool_calls}),
                    'artifacts':len(engine.repo.list_artifacts(rid))}
            assert item['status']=='completed' and item['steps']==5 and item['toolCalls']==8 and item['artifacts']==1, item
            reports.append(item)
        report.update(passed=True, seedCounts=counts, harnessMigration=harness_migration,
                      baselineMigration=baseline_migration, contextMigration=context_migration, baselines=baselines,
                      concurrentRuns=reports,
                      elapsedSeconds=round(time.perf_counter()-start,3),
                      performanceScope='two-run functional concurrency smoke check; not a production load benchmark')
    finally:
        if created:
            admin = psycopg2.connect(**admin_config)
            try:
                admin.autocommit = True
                with admin.cursor() as cur:
                    cur.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
                report['temporaryDatabaseDropped'] = True
            finally:
                admin.close()
        output = ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
