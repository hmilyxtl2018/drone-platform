class HumanApprovalRequired(RuntimeError):
    def __init__(self, approval_id, action):
        self.approval_id = approval_id
        self.action = action
        super().__init__(f"approval required for {action}: {approval_id}")


class ApprovalService:
    def __init__(self, repository): self.repository=repository
    def request(self, run_id, action, reason, requested_by, step_key=None):
        return self.repository.request_approval(run_id,action,reason,requested_by,step_key)
    def decide(self, approval_id, approved, reviewer, comment=''):
        return self.repository.decide_approval(approval_id,approved,reviewer,comment)
