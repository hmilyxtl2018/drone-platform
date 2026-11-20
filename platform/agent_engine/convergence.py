from .domain import RunStatus
class ConvergenceEvaluator:
 def evaluate(self,ctx):
  policy=ctx.profile.convergence; conditions=policy.get('completion_conditions_json',[])
  if isinstance(conditions,str):
   import json; conditions=json.loads(conditions or '[]')
  state=ctx.state
  if state.get('blockingConflict'): return RunStatus.REQUIRES_HUMAN
  if state.get('evidenceGap') or state.get('capabilityGaps'): return RunStatus.COMPLETED_WITH_GAPS
  if not ctx.output: return RunStatus.COMPLETED_WITH_GAPS
  if all(state.get('conditions',{}).get(str(i),False) for i,_ in enumerate(conditions)): return RunStatus.COMPLETED
  return RunStatus.COMPLETED_WITH_GAPS
