class RecoveryService:
    """Find interrupted runs and move them back to a safe queued state."""
    def __init__(self, repository): self.repository=repository
    def recover(self, run_id):
        run=self.repository.get_run(run_id)
        if not run: raise ValueError('run not found')
        if run['status'] in {'queued','running','waiting-tool','retrying','looping','planning','validating'} and hasattr(self.repository,'recover_safe_action_children'):
            self.repository.recover_safe_action_children(run_id)
        if run['status'] not in {'running','waiting-tool','retrying','looping','planning','validating'}:
            return run
        with self.repository.execution_lock(run_id):
            self.repository.transition(run_id,'queued',error='recovered after restart',recovery=True)
            self.repository.mark_interrupted_steps(run_id)
            self.repository.append_event(run_id,'run.recovered',detail={'from':run['status']})
        return self.repository.get_run(run_id)
