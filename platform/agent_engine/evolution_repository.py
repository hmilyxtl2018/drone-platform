"""Transactional terminal outbox and first-batch reflection records."""
from datetime import datetime,timedelta,timezone
from psycopg2.extras import Json
from .repository import new_id
from .task_intent import digest,read,admission_actor,parse_deadline
from .evolution_policy import validate_policy,assert_supported_agent


def installed(cursor):
    cursor.execute("SELECT to_regclass(current_schema() || '.agent_run_terminal_outbox') AS name")
    return bool(cursor.fetchone()['name'])


def capture_terminal(cursor,run_id,status,output):
    if status not in {'completed','completed-with-gaps','requires-human-review','failed','blocked','cancelled'} or not installed(cursor):
        return
    cursor.execute('''SELECT r.*,p.profile_hash,pr.policy_id,pr.document_json FROM agent_run r
        JOIN agent_run_profile_snapshot p USING(run_id) JOIN agent_evolution_policy a USING(agent_key)
        JOIN agent_evolution_policy_revision pr USING(policy_id) WHERE r.run_id=%s''',(run_id,))
    row=cursor.fetchone()
    if not row or not row['document_json'].get('enabled') or row.get('agent_layer')!='application':return
    cursor.execute('SELECT 1 FROM agent_evolution_run_binding WHERE run_id=%s',(run_id,))
    if cursor.fetchone():return
    cursor.execute('SELECT 1 FROM agent_improvement_candidate_run WHERE run_id=%s',(run_id,))
    if cursor.fetchone():return
    cursor.execute('SELECT parent_run_id FROM agent_run_budget_member WHERE run_id=%s',(run_id,))
    member=cursor.fetchone()
    if member and member['parent_run_id']:return
    intent=read(cursor,run_id)
    if intent['status']!='active' or intent['document'].get('purpose')!='public-research':return
    value=output if output is not None else row.get('output_json') or {}
    cursor.execute('''INSERT INTO agent_run_terminal_outbox(outbox_id,source_run_id,policy_id,source_status,
        source_profile_hash,source_output_hash,source_output_json) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
        (new_id('outbox'),run_id,row['policy_id'],status,row['profile_hash'],digest(value),Json(value)))


class EvolutionRepository:
    def __init__(self,factory):self.factory=factory

    def configure(self,agent_key,value,actor):
        if actor.kind!='human' or not actor.permits('admin'):raise PermissionError('human administrator required')
        assert_supported_agent(agent_key)
        document={**validate_policy(value),'agentKey':agent_key}
        with self.factory() as con:
            with con.cursor() as c:
                if document['mode']=='auto-trial':
                    from .evolution_trial import installed as trials_installed
                    if not trials_installed(c):raise ValueError('evolution trial migration 079 is required')
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('evolution-policy:'+agent_key,))
                c.execute('SELECT COALESCE(max(revision),0)+1 AS revision FROM agent_evolution_policy_revision WHERE agent_key=%s',(agent_key,))
                revision=c.fetchone()['revision'];pid=new_id('evolution-policy')
                c.execute('''INSERT INTO agent_evolution_policy_revision(policy_id,agent_key,revision,document_json,content_hash,created_by)
                    VALUES(%s,%s,%s,%s,%s,%s)''',(pid,agent_key,revision,Json(document),digest(document),actor.principal_id))
                c.execute('''INSERT INTO agent_evolution_policy(agent_key,policy_id) VALUES(%s,%s)
                    ON CONFLICT(agent_key) DO UPDATE SET policy_id=EXCLUDED.policy_id,updated_at=now()''',(agent_key,pid))
        return self.policy(agent_key)

    def start_trial(self,source_run_id,actor):
        """Explicit human request; never automatically backfill old tasks."""
        if actor.kind!='human' or not actor.permits('admin'):raise PermissionError('human administrator required')
        with self.factory() as con:
            with con.cursor() as c:
                c.execute('SELECT agent_key,status,output_json FROM agent_run WHERE run_id=%s',(source_run_id,));run=c.fetchone()
                if not run or run['status'] not in {'completed','completed-with-gaps'}:
                    raise ValueError('a delivered source Run is required')
                assert_supported_agent(run['agent_key'])
                from .task_intent import assert_dispatch
                assert_dispatch(c,source_run_id,operation='read-evidence')
                c.execute('''SELECT p.* FROM agent_evolution_policy a JOIN agent_evolution_policy_revision p USING(policy_id)
                    WHERE a.agent_key=%s''',(run['agent_key'],));policy=c.fetchone()
                if not policy or not policy['document_json']['enabled'] or policy['document_json']['mode']!='auto-trial':
                    raise ValueError('enable auto-trial for this Agent first')
                capture_terminal(c,source_run_id,run['status'],run['output_json'])
                # An explicit human retry can re-admit a failed preparation.
                # A cycle that has already started remains exactly once.
                c.execute('''UPDATE agent_run_terminal_outbox o SET status='pending',reason=NULL,processed_at=NULL
                    WHERE o.source_run_id=%s AND o.policy_id=%s AND o.status='skipped'
                    AND o.reason LIKE 'cycle-admission-%%' AND NOT EXISTS
                    (SELECT 1 FROM agent_evolution_cycle e WHERE e.outbox_id=o.outbox_id)''',
                    (source_run_id,policy['policy_id']))
        return self.view(source_run_id)

    def policy(self,agent_key):
        with self.factory() as con:
            with con.cursor() as c:
                if not installed(c):return {'agentKey':agent_key,'installed':False,'enabled':False}
                c.execute('''SELECT p.* FROM agent_evolution_policy a JOIN agent_evolution_policy_revision p USING(policy_id)
                    WHERE a.agent_key=%s''',(agent_key,));row=c.fetchone()
                return dict(row) if row else {'agentKey':agent_key,'enabled':False,'installed':True}

    @staticmethod
    def event(c,cycle_id,kind,detail):
        c.execute('INSERT INTO agent_evolution_event(cycle_id,event_type,detail_json) VALUES(%s,%s,%s)',
            (cycle_id,kind,Json(detail)))

    def view(self,source_run_id):
        from .evolution_budget import usage
        with self.factory() as con:
            with con.cursor() as c:
                if not installed(c):return {'runId':source_run_id,'cycles':[],'installed':False}
                c.execute('''SELECT e.*,b.run_id AS reflection_run_id,r.status AS reflection_status,r.error_message,
                    r.output_json AS reflection_json,p.document_json AS policy_json,q.limits_json
                    FROM agent_evolution_cycle e JOIN agent_evolution_policy_revision p USING(policy_id)
                    JOIN agent_evolution_budget q USING(cycle_id)
                    LEFT JOIN agent_evolution_run_binding b ON b.cycle_id=e.cycle_id AND b.role='reflection'
                    LEFT JOIN agent_run r ON r.run_id=b.run_id
                    WHERE e.source_run_id=%s ORDER BY e.created_at DESC LIMIT 10''',(source_run_id,))
                cycles=[dict(r) for r in c.fetchall()]
                for cycle in cycles:
                    cycle['usage']=usage(c,cycle['cycle_id'])
                    c.execute('SELECT event_type,detail_json,created_at FROM agent_evolution_event WHERE cycle_id=%s ORDER BY event_id',(cycle['cycle_id'],))
                    cycle['events']=list(c.fetchall())
                    c.execute('''SELECT b.role,b.run_id,r.status,r.model_call_count,r.tool_call_count,r.error_message
                        FROM agent_evolution_run_binding b JOIN agent_run r USING(run_id)
                        WHERE b.cycle_id=%s ORDER BY b.created_at,b.role''',(cycle['cycle_id'],))
                    cycle['runs']=list(c.fetchall())
                    from .evolution_trial import installed as trials_installed
                    if trials_installed(c):
                        c.execute('''SELECT p.proposal_id,p.candidate_hash,p.diff_json,p.rationale_json,
                            a.checks_json,a.passed FROM agent_evolution_proposal_binding b
                            JOIN agent_improvement_proposal p USING(proposal_id)
                            LEFT JOIN agent_improvement_evaluation a USING(proposal_id) WHERE b.cycle_id=%s''',(cycle['cycle_id'],))
                        trial=c.fetchone();cycle['trial']=dict(trial) if trial else None
                        if trial:
                            c.execute('''SELECT t.case_json,t.case_hash,t.evaluator_version,o.source_output_json
                                FROM agent_evolution_trial t JOIN agent_evolution_cycle e USING(cycle_id)
                                JOIN agent_run_terminal_outbox o USING(outbox_id) WHERE t.cycle_id=%s''', (cycle['cycle_id'],))
                            frozen=c.fetchone()
                            c.execute('''SELECT b.role,b.run_id,b.input_hash,r.status,r.input_json,r.output_json
                                FROM agent_evolution_run_binding b JOIN agent_run r USING(run_id)
                                WHERE b.cycle_id=%s AND b.role IN ('baseline','candidate')''', (cycle['cycle_id'],))
                            pair={row['role']:dict(row) for row in c.fetchall()}
                            from .decision_detail import comparison_details
                            cycle['trial']['comparisonDetails']=comparison_details(cycle,dict(frozen) if frozen else {},pair,
                                frozen['source_output_json'] if frozen else None)
                c.execute('SELECT agent_key FROM agent_run WHERE run_id=%s',(source_run_id,));run=c.fetchone()
                c.execute('SELECT status,reason FROM agent_run_terminal_outbox WHERE source_run_id=%s ORDER BY created_at DESC LIMIT 1',(source_run_id,))
                trigger=c.fetchone()
        policy=self.policy(run['agent_key']) if run else None
        return {'runId':source_run_id,'cycles':cycles,'policy':policy,
                'trigger':dict(trigger) if trigger else None,'mode':(policy or {}).get('document_json',{}).get('mode','observe-only'),
                'maxTrials':(policy or {}).get('document_json',{}).get('maxTrials',0),'automaticPublication':False}

    def stop(self,source_run_id,cycle_id,actor,reason):
        if actor.kind!='human' or not actor.permits('operator'):raise PermissionError('human operator required')
        if not isinstance(reason,str) or not 1<=len(reason.strip())<=1000:raise ValueError('stop reason required')
        with self.factory() as con:
            with con.cursor() as c:
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('evolution:'+cycle_id,))
                c.execute('''UPDATE agent_evolution_cycle SET status='stopped',stop_reason=%s,revision=revision+1,updated_at=now()
                    WHERE cycle_id=%s AND source_run_id=%s AND status IN ('reflecting','designing','trial-running','evaluating') RETURNING cycle_id''',
                    (reason.strip(),cycle_id,source_run_id))
                if c.fetchone():self.event(c,cycle_id,'cycle.stopped',{'reason':reason.strip(),'actor':actor.principal_id})
        return self.view(source_run_id)
