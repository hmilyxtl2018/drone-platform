from .domain import RunStatus
ALLOWED={
 'created':{'queued','waiting-human','failed','cancelled'},'queued':{'planning','waiting-human','failed','cancelled'},'planning':{'running','waiting-human','failed','cancelled'},
 'running':{'waiting-tool','waiting-human','retrying','looping','validating','completed','completed-with-gaps','requires-human-review','blocked','failed','cancelled'},
 'waiting-tool':{'running','waiting-human','retrying','failed','cancelled'},'waiting-human':{'running','requires-human-review','failed','cancelled'},'retrying':{'running','waiting-human','failed','cancelled'},'looping':{'running','waiting-human','requires-human-review','blocked','failed','cancelled'},'validating':{'completed','completed-with-gaps','requires-human-review','blocked','looping','waiting-human','failed','cancelled'},
 'completed':set(),'completed-with-gaps':set(),'requires-human-review':{'running','cancelled'},'blocked':set(),'failed':{'queued'},'cancelled':set()}
class StateMachine:
 def validate(self, current, target):
  c=current.value if hasattr(current,'value') else current; t=target.value if hasattr(target,'value') else target
  if c == t:
   return
  if t not in ALLOWED.get(c,set()): raise ValueError(f'illegal transition: {c} -> {t}')
 def validate_recovery(self,current):
  if current not in {'running','waiting-tool','retrying','looping','planning','validating'}:
   raise ValueError(f'cannot recover run in status: {current}')
