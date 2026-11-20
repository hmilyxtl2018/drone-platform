from __future__ import annotations

from agent_engine.application_identity import FUTURE_AGENT_KEYS
import hashlib
import json
class LoopController:
    def __init__(self, repository): self.repository=repository
    def evaluate(self, ctx):
        if not ctx.profile.loop_rules: return None
        if not ctx.state.get('evidenceGap'): return None
        rule=ctx.profile.loop_rules[0]
        # A depleted acquisition budget cannot yield new evidence in another
        # iteration. Complete the existing analysis/report once, then converge
        # with the preserved gap instead of generating repeated failed calls.
        archive = ctx.state.get('_evidenceArchive') or {}
        if (ctx.profile.agent_key in FUTURE_AGENT_KEYS
                and rule.get('return_step_key') == 'collect-public-evidence'
                and archive.get('acquisitionExhausted')):
            return {'action':'stop','status':rule['stop_action'],'reason':'acquisition_budget_exhausted'}
        ctx.iteration += 1
        progress=json.dumps({'evidence':ctx.state.get('evidence',[]),'findings':ctx.state.get('findings',[]),'gaps':ctx.state.get('capabilityGaps',[])},sort_keys=True,default=str)
        fingerprint=hashlib.sha256(progress.encode()).hexdigest()
        history=ctx.state.setdefault('_loop_progress',[])
        no_progress=int(ctx.state.get('_loop_no_progress',0))
        if history and history[-1] == fingerprint:
            no_progress += 1
        else:
            no_progress = 0
        history.append(fingerprint)
        ctx.state['_loop_no_progress']=no_progress
        if no_progress >= int(rule.get('no_progress_limit',2)):
            return {'action':'stop','status':rule['stop_action'],'reason':'no_progress_limit'}
        if ctx.iteration > int(rule['max_iterations']):
            return {'action':'stop','status':rule['stop_action'],'reason':'max_iterations'}
        return {'action':'loop','returnStepKey':rule['return_step_key'],'iteration':ctx.iteration,'reason':rule['trigger_condition']}
