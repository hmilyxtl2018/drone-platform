class TraceWriter:
    def __init__(self, repository): self.repository=repository
    def write(self, run_id, event_type, step_key=None, detail=None):
        self.repository.append_event(run_id,event_type,step_key,detail or {})
