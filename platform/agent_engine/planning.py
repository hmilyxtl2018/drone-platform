from __future__ import annotations


def dependency_batches(steps):
    """Order declared dependencies; retain legacy sequence groups without edges."""
    steps = list(steps)
    by_key = {step.key: step for step in steps}
    if len(by_key) != len(steps):
        raise ValueError('duplicate step keys')
    if not any(step.dependencies for step in steps):
        groups = []
        for step in sorted(steps, key=lambda item: (item.sequence, item.key)):
            if step.parallel_group and groups and groups[-1][0].parallel_group == step.parallel_group:
                groups[-1].append(step)
            else:
                groups.append([step])
        return groups
    for step in steps:
        if step.key in step.dependencies:
            raise ValueError('step cannot depend on itself: ' + step.key)
        missing = set(step.dependencies) - by_key.keys()
        if missing:
            raise ValueError('unknown step dependencies: ' + ', '.join(sorted(missing)))
    remaining, complete, groups = dict(by_key), set(), []
    while remaining:
        ready = sorted((step for step in remaining.values() if set(step.dependencies) <= complete),
                       key=lambda item: (item.sequence, item.key))
        if not ready:
            raise ValueError('execution graph contains a cycle')
        groups.append(ready)
        complete.update(step.key for step in ready)
        for step in ready:
            del remaining[step.key]
    return groups


class FixedPlanner:
    def plan(self, profile, state=None):
        """Return the frozen Profile in topological order."""
        return [step for group in dependency_batches(profile.steps) for step in group]

class PlanValidator:
    def validate(self, plan, profile):
        if not plan:
            raise ValueError('agent has no executable fixed steps')
        allowed={s.key for s in profile.steps}
        unknown=[getattr(s,'key',s) for s in plan if getattr(s,'key',s) not in allowed]
        if unknown: raise ValueError(f'plan contains steps outside profile: {unknown}')
        if len({s.key for s in plan}) != len(plan): raise ValueError('duplicate step keys')
        dependency_batches(plan)
        for rule in profile.loop_rules:
            if rule['return_step_key'] not in allowed: raise ValueError('loop return step is not in profile')
        return True

    def validate_runtime(self, profile):
        """Fail closed before persisting a fixed Run.

        DAG validity alone proves ordering, not that a node can produce its
        declared output.  Keep this check separate so research-v1 can use its
        own planner and legacy fixed UAV Profiles retain their reviewed
        built-in parser.
        """
        from .runtime_contracts import validate_fixed_runtime
        validate_fixed_runtime(profile)
        return True
