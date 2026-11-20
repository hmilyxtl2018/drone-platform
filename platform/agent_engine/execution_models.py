"""Safe, recorded model and tool identities for execution graph projections."""


def execution_method(*, models=(), tools=(), dispatches=(), delegations=(), kind=None,
                     observed=True, human=False):
    """Classify the execution boundary from audit facts, never a model budget.

    A dispatch identifies the local handler actually entered. Delegations prove
    orchestration only; a child's model calls are never direct parent calls.
    Unknown is a record-quality state, not an additional execution mechanism.
    """
    if kind == 'input':
        return {'category': 'input', 'label': '输入', 'basis': 'run-input',
                'description': '任务输入与冻结配置。输入来源以任务记录为准。'}
    if human:
        return {'category': 'human', 'label': '人工', 'basis': 'human-gate',
                'description': '人工操作或审核门禁；是否完成以审批记录为准。'}
    if delegations:
        return {'category': 'sub-agent', 'label': 'Sub Agent', 'basis': 'action-invocation',
                'description': '本节点已委派子 Agent；子运行中的模型与工具调用分别记录。'}
    if models:
        return {'category': 'llm', 'label': 'LLM', 'basis': 'model-call',
                'description': '本节点有直接模型调用记录。'}
    if tools or dispatches or delegations or kind in {'result', 'plan-validation'}:
        return {'category': 'fixed', 'label': '固定', 'basis':
                'tool-call' if tools else 'dispatch' if dispatches else 'delegation' if delegations else 'system-projection',
                'description': '固定执行：工具 / API、内部处理或子任务调度。'}
    return {'category': 'unknown', 'label': '未记录' if observed else '待执行',
            'basis': 'unrecorded' if observed else 'planned',
            'description': '执行方式未记录，不能仅凭没有模型调用记录认定为固定执行。' if observed else
                '配置节点尚未执行，实际执行方式将在调用发生后显示。'}


def dispatch_metadata(event):
    detail = event.get('detail_json') or {}
    return {key: detail.get(key) for key in ('stepRunId', 'executorKey', 'handlerId')} | {
        'createdAt': event.get('created_at')}


def delegation_metadata(row):
    return {'invocationId': row.get('invocation_id'), 'childRunId': row.get('child_run_id'),
            'agentKey': row.get('agent_key'), 'agentVersion': row.get('agent_version'),
            'displayName': row.get('display_name'),
            'parentStepRunId': row.get('parent_step_run_id'), 'status': row.get('status'),
            'createdAt': row.get('created_at'),
            'llmCalls': row.get('llmCalls', []), 'toolCalls': row.get('toolCalls', [])}


def model_call_metadata(call):
    """Keep requested and returned identities distinct; never infer a version."""
    return {
        "callId": call["call_id"],
        "runId": call.get("run_id"),
        "stepRunId": call.get("step_run_id"),
        "providerKey": call.get("provider_key"),
        "requestedModel": call.get("requested_model"),
        "actualModel": call.get("actual_model"),
        "status": call.get("status") or "unknown",
        "stage": call.get("stage"), "errorCode": call.get("error_code"),
        "createdAt": call.get("created_at"),
        **({'connectorBinding': call['connectorBinding']} if call.get('connectorBinding') else {}),
    }


def tool_call_metadata(call):
    """Expose the invoked tool version, without arguments or raw outputs."""
    return {
        "callId": call["call_id"],
        "runId": call.get("run_id"),
        "stepRunId": call.get("step_run_id"),
        "toolKey": call.get("tool_key"),
        "toolVersion": call.get("tool_version"),
        "status": call.get("status") or "unknown",
        "createdAt": call.get("created_at"),
        **({'connectorBinding': call['connectorBinding']} if call.get('connectorBinding') else {}),
    }
