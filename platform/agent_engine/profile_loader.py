from .repository import PostgresRepository
class ProfileLoader:
    def __init__(self, repository: PostgresRepository): self.repository=repository
    def load(self, agent_key, version=None): return self.repository.load_profile(agent_key,version)
    def load_for_run(self, run_id): return self.repository.load_profile_snapshot(run_id)
