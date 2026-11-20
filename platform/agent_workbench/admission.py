"""Bind a workspace revision to the real Engine and queue in one transaction."""
from contextlib import contextmanager

from .workspaces import WorkspaceRepository, WorkspaceRunService


def build_workspace_run_service(engine, queue):
    factory = engine.base_connection_factory

    @contextmanager
    def configuration_read():
        # ProfileLoader reads several connections. Hold its matching shared
        # session lock until the admission transaction commits, so a config
        # publication cannot split profile loading and persistence.
        with factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock_shared(hashtextextended('agent-admin-profile-config',0))")
            try:
                yield
            finally:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock_shared(hashtextextended('agent-admin-profile-config',0))")

    def create(cursor, agent_key, data):
        # The outer context already owns the shared lock. Reacquiring it on a
        # different connection can deadlock behind a waiting exclusive writer.
        profile = engine.repo._load_profile_consistent(agent_key)
        if data.get('collaborate') and agent_key != 'uav-capability-analyst':
            raise ValueError('collaboration must start with the capability analyst')
        # Phase3Repository freezes the profile, entity scope and independent
        # root budget, using this exact admission transaction.
        return engine.repo.create_run(profile, data, _cursor=cursor)

    def enqueue(cursor, run_id):
        # Runtime rows are fenced as soon as a queue row exists. Record the
        # admission event first inside this same transaction, while the Run is
        # still unclaimed; then insert the queue row as the transaction's last
        # runtime mutation. A worker cannot claim it before this commit.
        engine.repo.event(cursor, run_id, 'workspace.run-admitted', None,
                          {'poolKey': queue.pool_key, 'inputBinding': 'immutable-workspace-revision'})
        cursor.execute('INSERT INTO agent_run_queue(run_id,pool_key) VALUES(%s,%s)',
                       (run_id, queue.pool_key))

    return WorkspaceRunService(WorkspaceRepository(factory), create, enqueue,
                               admission_context=configuration_read)
