"""Register immutable baselines for existing Profiles without changing their configuration."""
from __future__ import annotations

import argparse
import json

from agent_engine.execution_baseline import build_baseline, register_baseline
from agent_engine.postgres import connection_factory
from agent_admin.config_publication import ConfigPublicationService


def register(database='ontology_agent_admin'):
    if database != 'ontology_agent_admin' and not database.startswith('agent_engine_verify_'):
        raise ValueError('unsupported baseline migration database')
    result = []
    with connection_factory(database=database)() as con:
        with con.cursor() as cursor:
            ConfigPublicationService._config_lock(cursor, True)
            cursor.execute('SELECT agent_key,status FROM agent_definition ORDER BY agent_key')
            agents = list(cursor.fetchall())
            for agent in agents:
                key = agent['agent_key']
                document = ConfigPublicationService._export(cursor, key)
                harness = ConfigPublicationService._baseline_harness(cursor, key)
                graph = build_baseline(key, document, harness)
                baseline = register_baseline(cursor, key, graph,
                    status='published' if agent['status'] == 'published' else 'testing')
                result.append({'agentKey': key, 'baselineId': baseline['baselineId'],
                               'baselineVersion': baseline['baselineVersion'], 'graphHash': baseline['graphHash'],
                               'nodes': len(graph['nodes']), 'edges': len(graph['edges'])})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default='ontology_agent_admin')
    print(json.dumps(register(parser.parse_args().database), indent=2))
