"""Recorded Run provenance and one fail-closed visibility projection.

Ownership is supported only by server records. Client Run inputs, tenant labels,
budget-root hints and inferred similarities are never authorization evidence.
The projection does not mutate historical Runs or their frozen snapshots.
"""
from __future__ import annotations

from collections import defaultdict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import re

from psycopg2.extras import Json


SOURCE_KINDS = frozenset({'dsh','admin','autopilot','workspace','followup','delegation','handoff','engineering','evolution','system','unknown'})


@dataclass(frozen=True)
class OriginContext:
    actor: object = None
    source_kind: str = 'system'
    source_ref: str | None = None
    parent_run_id: str | None = None


_context = ContextVar('agent_run_origin', default=OriginContext())


@contextmanager
def origin_context(actor=None, source_kind='system', source_ref=None, parent_run_id=None):
    """Carry trusted server admission metadata, never a client request object."""
    if source_kind not in SOURCE_KINDS:
        raise ValueError('invalid Run origin kind')
    for value in (source_ref,parent_run_id):
        if value is not None and (not isinstance(value,str) or not value or len(value)>256):
            raise ValueError('invalid Run origin reference')
    token = _context.set(OriginContext(actor,source_kind,source_ref,parent_run_id))
    try:
        yield
    finally:
        _context.reset(token)


def _schema(cursor):
    cursor.execute('''SELECT table_name,column_name FROM information_schema.columns
                       WHERE table_schema=current_schema()''')
    result=defaultdict(set)
    for row in cursor.fetchall():
        result[row['table_name']].add(row['column_name'])
    return result


def installed(cursor):
    cursor.execute("SELECT to_regclass(current_schema() || '.agent_run_origin') AS name")
    return bool((cursor.fetchone() or {}).get('name'))


def _resolve(cursor, *, context_run=None, context=None):
    """Accumulate all supported owner claims before filtering by the caller.

    Reading only the caller's claims first would miss contradictory claims and
    could leak a Run linked to two principals. Lineage is an actual directed
    relation, independent of the shared-budget root.
    """
    schema=_schema(cursor)
    if not {'run_id'} <= schema.get('agent_run',set()):
        return {}
    def rows(statement,args=()):
        cursor.execute(statement,args)
        return [dict(row) for row in cursor.fetchall()]
    ids={row['run_id'] for row in rows('SELECT run_id FROM agent_run')}
    if not ids:
        return {}
    humans=set()
    if {'principal_id','kind'} <= schema.get('agent_admin_identity',set()):
        humans={row['principal_id'] for row in rows("SELECT principal_id FROM agent_admin_identity WHERE kind='human'")}
    claims=defaultdict(set)
    origins={}
    evidence=defaultdict(list)
    sources=defaultdict(list)
    parents=defaultdict(set)
    children=defaultdict(set)
    ownership_parents=defaultdict(set)
    tainted=set()

    def claim(rid,owner,kind,ref,proof):
        if rid not in ids or owner not in humans:
            return
        claims[rid].add(owner)
        sources[rid].append((kind,ref))
        if proof not in evidence[rid]:
            evidence[rid].append(proof)

    def edge(parent,child,kind,ref,*,lineage=True):
        if parent not in ids or child not in ids:
            return
        ownership_parents[child].add(parent)
        if lineage:
            parents[child].add(parent)
        children[parent].add(child)
        sources[child].append((kind,ref))
        evidence[child].append({'kind':kind,'reference':ref,'parentRunId':parent})

    if 'agent_run_origin' in schema:
        for row in rows('SELECT * FROM agent_run_origin'):
            rid=row['run_id']; origins[rid]=row
            if row.get('attribution_status')=='conflict':
                tainted.add(rid)
            if row.get('attribution_status') in {'recorded','derived'}:
                claim(rid,row.get('owner_principal_id'),row.get('source_kind','unknown'),row.get('source_ref'),
                      {'kind':'recorded-origin','runId':rid})
            if row.get('parent_run_id'):
                edge(row['parent_run_id'],rid,row.get('source_kind','delegation'),row.get('source_ref'))
    if {'dsh_session','dsh_turn'} <= schema.keys():
        for row in rows('''SELECT t.run_id,t.turn_id,s.principal_id,s.session_id FROM dsh_turn t
                           JOIN dsh_session s USING(session_id) WHERE t.run_id IS NOT NULL'''):
            claim(row['run_id'],row['principal_id'],'dsh',row['turn_id'],{'kind':'dsh-turn','turnId':row['turn_id']})
    if {'agent_autopilot_mission','agent_autopilot_run_binding'} <= schema.keys():
        for row in rows('''SELECT b.run_id,b.mission_id,m.principal_id FROM agent_autopilot_run_binding b
                           JOIN agent_autopilot_mission m USING(mission_id)'''):
            claim(row['run_id'],row['principal_id'],'autopilot',row['mission_id'],{'kind':'mission','missionId':row['mission_id']})
    if {'agent_run_task_intent','agent_task_intent_revision'} <= schema.keys():
        for row in rows('''SELECT b.run_id,b.parent_run_id,b.intent_id,r.issuer_id,r.issuer_kind
                           FROM agent_run_task_intent b JOIN agent_task_intent_revision r USING(intent_id)'''):
            if row.get('issuer_kind')=='human':
                claim(row['run_id'],row.get('issuer_id'),'admin',row['intent_id'],{'kind':'human-task-intent','intentId':row['intent_id']})
            if row.get('parent_run_id'):
                edge(row['parent_run_id'],row['run_id'],'delegation',row['intent_id'])
    if {'agent_run_followup'} <= schema.keys():
        columns_followup=schema['agent_run_followup']
        if {'run_id','previous_run_id','principal_id'} <= columns_followup:
            for row in rows('SELECT run_id,previous_run_id,principal_id FROM agent_run_followup'):
                claim(row['run_id'],row['principal_id'],'followup',row['previous_run_id'],
                      {'kind':'followup','runId':row['run_id'],'previousRunId':row['previous_run_id']})
                edge(row['previous_run_id'],row['run_id'],'followup',row['run_id'],lineage=False)
    if {'agent_workspace_run'} <= schema.keys():
        workspace_columns=schema['agent_workspace_run']
        if {'run_id','workspace_id'} <= workspace_columns:
            owner_column='actor_id' if 'actor_id' in workspace_columns else None
            if owner_column:
                for row in rows('SELECT run_id,workspace_id,actor_id FROM agent_workspace_run'):
                    claim(row['run_id'],row['actor_id'],'workspace',row['workspace_id'],
                          {'kind':'workspace-run','workspaceId':row['workspace_id']})
            elif {'agent_research_workspace'} <= schema.keys() and 'owner_id' in schema['agent_research_workspace']:
                for row in rows('''SELECT b.run_id,b.workspace_id,w.owner_id FROM agent_workspace_run b
                                   JOIN agent_research_workspace w USING(workspace_id)'''):
                    claim(row['run_id'],row['owner_id'],'workspace',row['workspace_id'],
                          {'kind':'workspace-owner','workspaceId':row['workspace_id']})
            if 'previous_run_id' in workspace_columns:
                for row in rows('SELECT run_id,previous_run_id,workspace_id FROM agent_workspace_run WHERE previous_run_id IS NOT NULL'):
                    edge(row['previous_run_id'],row['run_id'],'followup',row['workspace_id'],lineage=False)
    if {'run_id','event_type','detail_json'} <= schema.get('agent_run_event',set()):
        for row in rows("SELECT run_id,detail_json FROM agent_run_event WHERE event_type='admin.run-created'"):
            detail=row.get('detail_json') or {}
            if isinstance(detail,dict):
                claim(row['run_id'],detail.get('principalId'),'admin',row['run_id'],{'kind':'admin-run-created','runId':row['run_id']})
    for table,parent_column,child_column,kind in (
            ('agent_action_invocation','run_id','child_run_id','delegation'),
            ('agent_run_handoff','run_id','child_run_id','handoff'),
            ('agent_run_budget_member','parent_run_id','run_id','delegation')):
        if {parent_column,child_column} <= schema.get(table,set()):
            for row in rows(f'SELECT {parent_column} AS parent,{child_column} AS child FROM {table} WHERE {parent_column} IS NOT NULL'):
                edge(row['parent'],row['child'],kind,row['child'])
    if {'run_id','parent_run_id','principal_id'} <= schema.get('agent_engineering_run',set()):
        for row in rows('SELECT run_id,parent_run_id,principal_id FROM agent_engineering_run'):
            claim(row['run_id'],row['principal_id'],'engineering',row['run_id'],{'kind':'manual-engineering','runId':row['run_id']})
            edge(row['parent_run_id'],row['run_id'],'engineering',row['run_id'])
    if {'agent_improvement_candidate_run','agent_improvement_proposal'} <= schema.keys():
        for row in rows('''SELECT b.run_id,b.proposal_id,p.source_run_id FROM agent_improvement_candidate_run b
                           JOIN agent_improvement_proposal p USING(proposal_id)'''):
            edge(row['source_run_id'],row['run_id'],'evolution',row['proposal_id'],lineage=False)
    if {'agent_evolution_run_binding','agent_evolution_cycle'} <= schema.keys():
        for row in rows('''SELECT b.run_id,b.cycle_id,c.source_run_id FROM agent_evolution_run_binding b
                           JOIN agent_evolution_cycle c USING(cycle_id)'''):
            # Evolution is a separately budgeted root. This is an observation
            # link only: it does not add a budget-parent edge.
            edge(row['source_run_id'],row['run_id'],'evolution',row['cycle_id'],lineage=False)

    if context_run is not None and context is not None:
        actor=context.actor
        if actor is not None:
            owner=getattr(actor,'principal_id',None)
            if getattr(actor,'kind','human')!='human' or owner not in humans:
                raise PermissionError('Run origin requires a recorded human principal')
            claim(context_run,owner,context.source_kind,context.source_ref,{'kind':'admission-context'})
        if context.parent_run_id:
            if context.parent_run_id not in ids:
                raise ValueError('Run origin parent does not exist')
            edge(context.parent_run_id,context_run,context.source_kind,context.source_ref)

    # Topological propagation denies cyclic and contradictory ancestry. Claims
    # from every verified parent are retained so cross-owner edges cannot mask
    # an existing owner with whichever row happened to be processed first.
    indegree={rid:len(ownership_parents[rid]) for rid in ids}
    ready=deque(sorted(rid for rid in ids if indegree[rid]==0))
    ordered=[]
    while ready:
        rid=ready.popleft(); ordered.append(rid)
        if len(parents[rid])>1 or len(claims[rid])>1:
            tainted.add(rid)
        for child in children[rid]:
            claims[child].update(claims[rid])
            if rid in tainted:
                tainted.add(child)
            indegree[child]-=1
            if indegree[child]==0:
                ready.append(child)
    tainted.update(ids-set(ordered))
    result={}
    roots={}
    for rid in ordered:
        parent=next(iter(parents[rid]),None)
        roots[rid]=roots.get(parent,parent) if parent else rid
    # Specific server entry records take precedence over the generic human
    # TaskIntent/DSH transport evidence they may also produce.  This priority
    # is only for legacy rows with no persisted admission provenance.
    priority={'autopilot':0,'followup':1,'workspace':2,'evolution':3,'engineering':4,'dsh':5,'admin':6,'handoff':7,'delegation':8,'system':9,'unknown':10}
    for rid in ids:
        owner=next(iter(claims[rid]),None) if len(claims[rid])==1 else None
        recorded=origins.get(rid)
        candidates=sources[rid]
        if recorded and recorded.get('source_kind') in SOURCE_KINDS - {'unknown'}:
            # An immutable admission origin is the source of record, even if
            # another server table later links its Run to a DSH conversation.
            # Ownership still incorporates every claim and fails closed below.
            kind,ref=recorded['source_kind'],recorded.get('source_ref')
        else:
            kind,ref=min(candidates,key=lambda item:priority.get(item[0],99)) if candidates else ('unknown',None)
        parent=next(iter(parents[rid]),None) if len(parents[rid])==1 else None
        status='conflict' if rid in tainted or len(claims[rid])>1 else 'recorded' if owner and recorded and recorded.get('attribution_status')=='recorded' else 'derived' if owner else 'unknown'
        result[rid]={'runId':rid,'ownerPrincipalId':owner,'sourceKind':kind,'sourceRef':ref,
                     'parentRunId':parent,'lineageRootRunId':roots.get(rid),
                     'attributionStatus':status,'_evidence':evidence[rid]}
    return result


def visible_runs(cursor,actor):
    """Return only Runs owned by the caller with verified, consistent lineage."""
    principal=getattr(actor,'principal_id',None)
    if not isinstance(principal,str) or not principal or getattr(actor,'kind','human')!='human':
        return {}
    result=_resolve(cursor)
    return {rid:{key:value for key,value in row.items() if not key.startswith('_')}
            for rid,row in result.items() if row['ownerPrincipalId']==principal
            and row['attributionStatus'] in {'recorded','derived'}}


def record_origin(cursor,run_id,input_data):
    """Append new admission provenance in the caller's Run transaction.

    input_data is accepted for the common admission signature but deliberately
    not used as authority. An installed schema is strict: any failed provenance
    write aborts the Run transaction, rather than silently dropping the record.
    """
    if not installed(cursor):
        return None
    context=_context.get()
    resolved=_resolve(cursor,context_run=run_id,context=context)
    row=resolved.get(run_id)
    if row is None:
        raise ValueError('Run origin target does not exist')
    if row['attributionStatus']=='conflict':
        raise PermissionError('Run origin ownership or lineage conflicts')
    owner=row['ownerPrincipalId']
    if context.parent_run_id:
        parent=resolved.get(context.parent_run_id)
        if not parent:
            raise ValueError('Run origin parent does not exist')
        # An old Run may have no provenance. Preserve existing execution
        # compatibility: the child stays unknown unless this admission carries
        # an independently authenticated owner. A conflicting known parent is
        # still fail-closed.
        if parent['attributionStatus']=='conflict':
            raise PermissionError('Run origin parent ownership conflicts')
        if parent['ownerPrincipalId'] and context.actor is not None and parent['ownerPrincipalId'] != owner:
            raise PermissionError('Run origin parent owner conflicts with actor')
    source_kind=context.source_kind
    source_ref=context.source_ref
    if source_kind=='system' and row['sourceKind']!='unknown':
        source_kind,source_ref=row['sourceKind'],row['sourceRef']
    status=('recorded' if context.actor is not None else 'derived') if owner else 'unknown'
    cursor.execute('''INSERT INTO agent_run_origin(run_id,owner_principal_id,tenant_id,source_kind,source_ref,
        parent_run_id,lineage_root_run_id,attribution_status,evidence_refs_json)
        VALUES(%s,%s,NULL,%s,%s,%s,%s,%s,%s) ON CONFLICT(run_id) DO NOTHING RETURNING run_id''',
        (run_id,owner,source_kind,source_ref,row['parentRunId'],row['lineageRootRunId'],status,Json(row['_evidence'])))
    inserted=cursor.fetchone()
    if not inserted:
        cursor.execute('SELECT owner_principal_id,source_kind,source_ref,parent_run_id FROM agent_run_origin WHERE run_id=%s',(run_id,))
        previous=cursor.fetchone()
        if (not previous or previous['owner_principal_id']!=owner or previous['source_kind']!=source_kind
                or previous['source_ref']!=source_ref or previous['parent_run_id']!=row['parentRunId']):
            raise PermissionError('Run origin is already recorded differently')
    row['attributionStatus']=status
    row['sourceKind']=source_kind
    row['sourceRef']=source_ref
    return {key:value for key,value in row.items() if not key.startswith('_')}
