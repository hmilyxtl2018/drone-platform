"""Read-only projection of frozen plans and observed execution into an acyclic graph."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime

from agent_engine.execution_models import model_call_metadata, tool_call_metadata, execution_method, dispatch_metadata, delegation_metadata


TERMINAL = {"completed", "completed-with-gaps", "requires-human-review", "blocked", "failed", "cancelled"}
ACTION_LABELS = {"report": "提出生成报告", "request-review": "提出人工审核", "stop": "提出停止研究"}
TOOL_LABELS = {"ontology.get_entity": "读取本体数据", "ontology.query_evidence": "查询本体证据",
               "ontology.resolve_entity": "定位目标机型", "web.search": "检索公开来源", "web.scrape": "抓取来源原文"}
WORKER_INTERRUPTED = "worker interrupted; recover with a new attempt"
SUCCESS = {"succeeded", "completed", "completed-with-gaps"}


def _output_summary(record):
    """Summarize only the recorded output, never infer content from status."""
    metrics = record.get("output_metrics")
    if isinstance(metrics, dict):
        key = record.get("step_key")
        values = {
            "define-research-question": ("perspectiveCount", "个研判视角"),
            "generate-research-hypotheses": ("hypothesisCount", "个研究问题"),
            "plan-source-coverage": ("sourcePlanCount", "个来源规划项"),
            "collect-public-evidence": ("sourceCount", "个来源"),
            "extract-facts": ("evidenceCount", "条引文"),
            "fuse-evidence-and-conflicts": ("evidenceCount", "条引文"),
            "extract-war-system": ("systemFindingCount", "项体系要素"),
            "analyze-by-perspective": ("perspectiveFindingCount", "个视角结果"),
            "red-team-challenge": ("auditFindingCount", "项审查结果"),
        }
        if key == "collect-public-evidence":
            return f"{metrics.get('sourceCount', 0)} 个来源 · {metrics.get('searchCount', 0)} 条检索"
        if key in {"extract-facts", "fuse-evidence-and-conflicts"}:
            extra = f" · {metrics.get('conflictCount', 0)} 项冲突" if metrics.get("conflictCount") else ""
            return f"{metrics.get('evidenceCount', 0)} 条引文{extra}"
        if key == "compose-judgment-report":
            return f"{metrics.get('reportEvidenceCount', 0)} 条引文 · {metrics.get('reportAssertionCount', 0)} 条关系"
        if key == "human-review" and metrics.get("publicationStatus"):
            return "草稿已生成 · 发布前审阅"
        if key == "generate-research-hypotheses" and metrics.get('researchQuestionCount') is not None:
            return f"{metrics['researchQuestionCount']} 个研究问题"
        if key in values:
            field, suffix = values[key]
            return f"{metrics.get(field, 0)} {suffix}"
        return None
    output = record.get("output_json") or {}
    if not isinstance(output, dict):
        return None
    key = record.get("step_key")
    bundle = output.get("sourceBundle") or {}
    report = output.get("judgmentReport") or {}
    if key == "define-research-question" and "perspectives" in output:
        return f"{len(output['perspectives'])} 个研判视角"
    if key == "generate-research-hypotheses" and "researchQuestions" in output:
        return f"{len(output['researchQuestions'])} 个研究问题"
    if key == "generate-research-hypotheses" and "hypotheses" in output:
        return f"{len(output['hypotheses'])} 个研究问题"
    if key == "plan-source-coverage" and "sourcePlan" in output:
        funded = sum(item.get("status") != "budget-unfunded" for item in output["sourcePlan"])
        return f"{funded} / {len(output['sourcePlan'])} 视角已规划"
    if key == "collect-public-evidence" and "sources" in bundle:
        return f"{len(bundle['sources'])} 个来源 · {len(bundle.get('searches', []))} 条检索记录"
    if key in {"extract-facts", "fuse-evidence-and-conflicts"} and "evidence" in output:
        extra = f" · {len(output['conflicts'])} 项冲突" if "conflicts" in output else ""
        return f"{len(output['evidence'])} 条引文" + extra
    if key == "extract-war-system" and "systemFindings" in output:
        return f"{len(output['systemFindings'])} 项体系要素"
    if key == "analyze-by-perspective" and "perspectiveFindings" in output:
        values = output["perspectiveFindings"]
        supported = sum(item.get("status") == "candidate" for item in values)
        relations = sum(len(item.get("relations", [])) for item in values)
        return f"{supported} / {len(values)} 视角 · {relations} 条关系"
    if key == "red-team-challenge" and "auditFindings" in output:
        return f"{len(output['auditFindings'])} 项审查结果"
    if key == "compose-judgment-report" and report:
        return f"{len(report.get('evidence', []))} 条引文 · {len((report.get('ontology') or {}).get('assertions', []))} 条关系"
    if key == "human-review" and output.get("publicationStatus"):
        return "草稿已生成 · 发布前审阅"
    return None


def _time(value):
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def build_execution_graph(run, profile, *, plans=(), steps=(), model_calls=(), tool_calls=(),
                          approvals=(), artifacts=(), events=(), contexts=(), actions=(), skill_invocations=()):
    """Use only the run's snapshot. Never promote a proposed action to executed.

    Data-contract edges are solid and carry the producer/consumer contract
    metadata. Execution-dependency edges are dashed; observed temporal order,
    retries and context links retain their own lighter visual styles. No
    reference-catalog operation is treated as an executable step.
    """
    plans = sorted(plans, key=lambda p: p.get("plan_version", 0))
    steps = sorted(steps, key=lambda s: (_time(s.get("created_at")), s.get("attempt", 0)))
    events = sorted(events, key=lambda e: e["event_id"])
    research = profile.model_policy.get("config_json", {}).get("engineMode") == "research-v1"
    # Publication review is a post-run concern for unattended drafts. Keep
    # that distinction in the graph contract so the UI never presents a
    # completed automatic run as waiting for an operator.
    raw_automation_mode = (run.get("input_json") or {}).get("automationMode")
    # Runs created before unattended-draft was introduced have no mode field.
    # Preserve their historical human gate instead of rewriting the audit view.
    automation_mode = raw_automation_mode or ("human-gate" if run.get("status") == "waiting-human" else "unattended-draft")
    unattended_draft = automation_mode in {"unattended-draft", "expert-draft"}
    accepted = [p for p in plans if p.get("validation_json", {}).get("accepted") is True]
    context_by_id = {item.get("context_id"): item for item in contexts if item.get("context_id")}
    groups = [{"id": "input", "label": "任务与输入"}]
    groups.append({"id": "baseline" if research else "fixed", "label": "建立数据基线" if research else "冻结工作流"})
    groups.extend({"id": p["plan_id"], "label": "研究计划 v" + str(p["plan_version"]) +
                   ("" if p.get("validation_json", {}).get("accepted") is True else " · 校验未通过"),
                   "availableAt": p.get("created_at")} for p in plans)
    if research:
        groups.append({"id": "report", "label": "报告与交付校验"})
    groups.append({"id": "result", "label": "运行结果"})
    nodes, edges, warnings = [], [], []
    edge_keys, adjacency = set(), defaultdict(set)

    def node(node_id, label, group, kind="step", status="pending", **values):
        item = {"id": node_id, "label": label, "group": group, "kind": kind, "status": status, **values}
        nodes.append(item)
        return item

    def edge(source, target, kind="dependency", **metadata):
        edge_key = (source, target, kind)
        if source == target or edge_key in edge_keys:
            return
        # In interrupted/looped legacy runs an original dependency may describe
        # another iteration. Keep the projection acyclic and disclose the gap.
        todo, seen = [target], set()
        while todo:
            key = todo.pop()
            if key == source:
                warnings.append("跨迭代依赖无法唯一还原，已保留独立尝试记录。")
                return
            if key not in seen:
                seen.add(key)
                todo.extend(adjacency[key])
        adjacency[source].add(target)
        edge_keys.add(edge_key)
        edges.append({"from": source, "to": target, "kind": kind, **metadata})

    def contract_info(contract):
        if not isinstance(contract, dict) or not contract:
            return None
        properties = contract.get("properties") if isinstance(contract.get("properties"), dict) else {}
        required = contract.get("required") if isinstance(contract.get("required"), list) else []
        return {
            "name": contract.get("title") or contract.get("name") or contract.get("$id") or "inline-object",
            "version": contract.get("schemaVersion") or contract.get("contractVersion") or "inline",
            "requiredFields": [str(item) for item in required],
            "fields": sorted(str(item) for item in properties),
            "schema": deepcopy(contract),
        }

    def data_contract(source, target):
        produced = contract_info(source.get("outputContract"))
        consumed = contract_info(target.get("inputContract"))
        if not produced and not consumed:
            return None
        produced_fields = set(produced["fields"] if produced else [])
        consumed_fields = set(consumed["fields"] if consumed else [])
        return {
            "name": (consumed or produced)["name"],
            "version": (consumed or produced)["version"],
            "producer": {
                "nodeId": source["id"], "object": source.get("label"),
                "contract": produced,
            },
            "consumer": {
                "nodeId": target["id"], "object": target.get("label"),
                "contract": consumed,
            },
            "fieldMapping": {
                "matchedFields": sorted(produced_fields & consumed_fields),
                "producerFields": sorted(produced_fields),
                "consumerFields": sorted(consumed_fields),
                "unmatchedConsumerFields": sorted(consumed_fields - produced_fields),
                "mode": "schema-field-overlap" if produced and consumed else "partial-contract",
            },
            "validationStatus": "declared" if produced and consumed else "partial",
        }

    node("entry", "任务输入与冻结配置", "input", "input", "succeeded", availableAt=run.get("created_at"),
         finishedAt=run.get("created_at"), executionKey=None, observed=True)
    for plan in plans:
        if plan.get("validation_json", {}).get("accepted") is not True:
            node("plan-validation:" + plan["plan_id"], "计划校验未通过", plan["plan_id"], "plan-validation", "blocked",
                 planId=plan["plan_id"], reason=plan.get("validation_json", {}).get("code"),
                 observed=True, createdAt=plan.get("created_at"), availableAt=plan.get("created_at"),
                 finishedAt=plan.get("created_at"), executionKey=None)
    definitions, dependency_keys = {}, {}
    if not research:
        for step in profile.steps:
            label = "发布准备（运行外审阅）" if step.key == "human-review" and unattended_draft else step.name
            definitions[step.key] = {"label": label, "group": "fixed", "kind": "step",
                                     "tools": list(step.tools), "required": step.required,
                                     "inputContract": deepcopy(step.input_contract),
                                     "outputContract": deepcopy(step.output_contract),
                                     "availableAt": run.get("created_at")}
            dependency_keys[step.key] = list(step.dependencies)
    for plan in accepted:
        for item in plan.get("plan_json", {}).get("nodes", []):
            key = plan["plan_id"] + "-" + item["id"]
            definitions[key] = {"label": TOOL_LABELS.get(item.get("toolKey"), item.get("toolKey")) or
                                ACTION_LABELS.get(item.get("action"), item["id"]),
                                "group": plan["plan_id"], "kind": "decision" if item.get("action") != "tool" else "step",
                                "tools": [item["toolKey"]] if item.get("toolKey") else [],
                                "inputContract": deepcopy(getattr(getattr(profile, "tools", {}).get(item.get("toolKey")), "input_schema", {}) or {}),
                                "outputContract": deepcopy(getattr(getattr(profile, "tools", {}).get(item.get("toolKey")), "output_schema", {}) or {}),
                                "planId": plan["plan_id"], "planNodeId": item["id"],
                                "reason": item.get("reason"), "arguments": item.get("arguments", {}),
                                "availableAt": plan.get("created_at"), "action": item.get("action")}
            dependency_keys[key] = [plan["plan_id"] + "-" + dep for dep in item.get("dependsOn", [])]

    by_key, by_step_id = defaultdict(list), {}

    def research_group(at):
        prior = [p for p in accepted if _time(p.get("created_at")) <= _time(at)]
        return prior[-1]["plan_id"] if prior else "baseline"

    for record in steps:
        key = record["step_key"]
        definition = dict(definitions.get(key, {}))
        if not definition:
            label = {"research-read-ontology": "读取本体数据"}.get(key, key)
            group = research_group(record.get("created_at")) if research else "fixed"
            if key.endswith("-validate-materials"):
                label, group = "校验研究材料", "report" if research else "fixed"
            elif key.endswith("-validate-deliverables"):
                label, group = "校验交付物", "report" if research else "fixed"
            definition = {"label": label, "group": group, "kind": "step"}
        definition.setdefault("availableAt", run.get("created_at") if not research else record.get("created_at"))
        item = node(record["step_run_id"], status=record["status"], **definition,
                    executionKey=key, stepRunId=record["step_run_id"], attempt=record.get("attempt", 0),
                    observed=True, observedStatus=record["status"], startedAt=record.get("started_at"), finishedAt=record.get("finished_at"),
                    createdAt=record.get("created_at"), error=record.get("error_message"))
        interrupted = record.get("error_message") == WORKER_INTERRUPTED
        item["failureKind"] = "worker-interrupted" if interrupted else "execution-error" if record["status"] == "failed" else None
        item["outputSummary"] = _output_summary(record)
        recorded_output = record.get('output_json')
        invocation = record.get('action_invocation') or (recorded_output.get('actionInvocation') if isinstance(recorded_output, dict) else None)
        item['delegations'] = [invocation] if isinstance(invocation, dict) and invocation.get('childRunId') else []
        recorded_actions = [delegation_metadata(a) for a in actions if a.get('parent_step_run_id') == record['step_run_id']]
        if recorded_actions:
            item['delegations'] = recorded_actions
        item['dispatches'] = [dispatch_metadata(e) for e in events if e.get('event_type') == 'step.execution'
                              and (e.get('detail_json') or {}).get('stepRunId') == record['step_run_id']]
        item["elapsedMs"] = round(max(0, _time(record["finished_at"]) - _time(record["started_at"])) * 1000) if record.get("finished_at") and record.get("started_at") else None
        item["elapsedIncludesDowntime"] = interrupted
        output = record.get("output_json") or {}
        metrics = record.get("output_metrics") if isinstance(record.get("output_metrics"), dict) else {}
        item["gapCount"] = metrics.get("gapCount", len(output.get("gaps", [])) if isinstance(output, dict) else 0)
        item["gapTypeCount"] = len({g.get("reason") for g in output.get("gaps", []) if isinstance(g, dict) and g.get("reason")}) if isinstance(output, dict) else None
        by_key[key].append(item)
        by_step_id[record["step_run_id"]] = item

    for key, definition in definitions.items():
        if by_key[key]:
            continue
        # A terminal plan action is a proposal, not a recorded tool/step run.
        # Even if the whole run is complete, keep its status explicitly unobserved.
        item = node("planned:" + key, status="proposed" if definition["kind"] == "decision" else "pending",
                    **definition, executionKey=key, observed=False)
        by_key[key].append(item)

    for key, values in by_key.items():
        values.sort(key=lambda n: (n.get("attempt", 0), _time(n.get("createdAt"))))
        for before, after in zip(values, values[1:]):
            edge(before["id"], after["id"], "retry")
        for index, item in enumerate(values):
            if item.get("status") != "failed":
                continue
            recovered = next((later for later in values[index + 1:] if later.get("status") in SUCCESS and
                              _time(later.get("createdAt")) >= _time(item.get("finishedAt"))), None)
            item["recoveredBy"] = recovered["id"] if recovered else None
            item["recoveredAt"] = recovered.get("finishedAt") if recovered else None
        for target in values:
            for dep in dependency_keys.get(key, []):
                candidates = by_key.get(dep, [])
                if not candidates:
                    warnings.append("冻结计划中的前置节点缺失：" + dep)
                    continue
                # Pick the completed instance preceding this attempt. A later
                # iteration must never become a prerequisite of an earlier one.
                prior = [n for n in candidates if n.get("finishedAt") and
                         _time(n["finishedAt"]) <= _time(target.get("startedAt"))]
                if target.get("observed"):
                    if prior:
                        producer = prior[-1]
                        edge(producer["id"], target["id"], "dependency",
                             **({"dependencyType": "declared", "contractStatus": "declared" if data_contract(producer, target) else "not-declared"} if contexts else {}))
                        contract = data_contract(producer, target)
                        if contract:
                            edge(producer["id"], target["id"], "data-contract", contract=contract)
                    elif not target.get("startedAt") or all(not n.get("observed") for n in candidates):
                        producer = candidates[-1]
                        edge(producer["id"], target["id"], "dependency",
                             **({"dependencyType": "declared", "contractStatus": "declared" if data_contract(producer, target) else "not-declared"} if contexts else {}))
                        contract = data_contract(producer, target)
                        if contract:
                            edge(producer["id"], target["id"], "data-contract", contract=contract)
                    else:
                        warnings.append("部分前置关系缺少同次执行时间记录，未推断跨轮依赖。")
                else:
                    producer = candidates[-1]
                    edge(producer["id"], target["id"], "dependency",
                         **({"dependencyType": "declared", "contractStatus": "declared" if data_contract(producer, target) else "not-declared"} if contexts else {}))
                    contract = data_contract(producer, target)
                    if contract:
                        edge(producer["id"], target["id"], "data-contract", contract=contract)

    for call in model_calls:
        sid = call.get("step_run_id")
        if sid in by_step_id:
            continue
        stage = call.get("stage") or "model"
        group = research_group(call.get("created_at")) if research else "fixed"
        label = "模型调用 · " + stage
        if stage == "dynamic-plan":
            following = [p for p in plans if _time(p.get("created_at")) >= _time(call.get("created_at"))]
            group = following[0]["plan_id"] if following else group
            label = "生成研究计划"
        elif stage == "research-report":
            group, label = "report" if research else "fixed", "生成研究报告"
        elif stage.startswith("extract-"):
            label = "提取原文证据"
        duration = max(0, call.get("latency_ms") or 0) / 1000
        finished = _time(call.get("created_at"))
        start = datetime.fromtimestamp(finished - duration).astimezone().isoformat() if finished else None
        node("model:" + call["call_id"], label, group, "model", call.get("status") or "unknown",
             observed=True, modelCallId=call["call_id"], stage=stage, startedAt=start,
             startedAtEstimated=True, finishedAt=call.get("created_at"), createdAt=call.get("created_at"),
             availableAt=start, executionKey=None)

    output_artifacts = [a for a in artifacts if a.get("artifact_type") == "agent-output"]
    node("result", "运行收敛与交付", "result", "result", run.get("status") if run.get("status") in TERMINAL else "pending",
         observed=run.get("status") in TERMINAL, finishedAt=run.get("completed_at"),
         availableAt=run.get("created_at"), artifactIds=[a["artifact_id"] for a in output_artifacts], executionKey=None)

    # Dashed lines express observed order only. They do not invent dependency
    # edges between parallel tasks. Connect an instance to the nearest completed
    # predecessor, never to an overlapping attempt.
    actual = sorted([n for n in nodes if n.get("observed") and n["id"] not in {"entry", "result"}],
                    key=lambda n: (_time(n.get("startedAt") or n.get("finishedAt") or n.get("createdAt")), n["id"]))
    for index, item in enumerate(actual):
        if any(e["to"] == item["id"] for e in edges):
            continue
        start = _time(item.get("startedAt") or item.get("finishedAt") or item.get("createdAt"))
        prior = [n for n in actual[:index] if n.get("finishedAt") and _time(n["finishedAt"]) <= start]
        edge(max(prior, key=lambda n: _time(n["finishedAt"]))["id"] if prior else "entry", item["id"], "observed-order")
    for item in nodes:
        if item["id"] != "entry" and not any(e["to"] == item["id"] for e in edges):
            if item["id"] == "result":
                finished = [n for n in nodes if n["id"] not in {"entry", "result"} and
                            not any(e["from"] == n["id"] for e in edges)]
                for predecessor in finished or [nodes[0]]:
                    edge(predecessor["id"], "result", "context")
            else:
                edge("entry", item["id"], "context")

    # Bind only explicit call/step/artifact references. Unassigned human reviews
    # remain run-level rather than being attached to an arbitrary graph node.
    saved_artifact_ids = {a["artifact_id"] for a in artifacts}
    def output_artifact_refs(value):
        result = set()
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"artifactId", "sourceArtifactId", "outputArtifactId"} and isinstance(child, str) and child in saved_artifact_ids:
                    result.add(child)
                elif key == "deliverables" and isinstance(child, dict):
                    result.update(v for v in child.values() if isinstance(v, str) and v in saved_artifact_ids)
                result.update(output_artifact_refs(child))
        elif isinstance(value, list):
            for child in value:
                result.update(output_artifact_refs(child))
        return result

    for item in nodes:
        sid = item.get("stepRunId")
        related_calls = [c for c in model_calls if c.get("call_id") == item.get("modelCallId") or
                         (sid and c.get("step_run_id") == sid)]
        item['skillBindings'] = [{k:v for k,v in b.items() if k != 'package'} for b in (getattr(profile,'skill_manifest',{}) or {}).get('bindings',[])
                                 if b['stepKey'] == item.get('executionKey')]
        # Match an exact attempt or a recorded call; never just a phase name.
        item['skillInvocations'] = [dict(i) for i in skill_invocations if i.get('run_id') == run['run_id'] and
            ((sid and i.get('step_run_id') == sid) or
             any(c['call_id'] in i.get('model_call_ids',[]) for c in related_calls))]
        item["modelCallIds"] = [c["call_id"] for c in related_calls]
        item["llmCalls"] = [model_call_metadata(c) for c in related_calls]
        related_tool_calls = [c for c in tool_calls if sid and c.get("step_run_id") == sid]
        item["toolCallIds"] = [c["call_id"] for c in related_tool_calls]
        item["toolCalls"] = [tool_call_metadata(c) for c in related_tool_calls]
        item["modelCallCount"] = len(item["modelCallIds"])
        item["toolCallCount"] = len(item["toolCallIds"])
        item["toolFailureCount"] = sum(1 for c in tool_calls if sid and c.get("step_run_id") == sid and c.get("status") in {"failed", "timeout"})
        item["modelFailureCount"] = sum(1 for c in related_calls if c.get("status") in {"failed", "timeout"})
        related_contexts = [c for c in contexts if (sid and c.get("step_run_id") == sid)]
        related_contexts += [c for c in contexts if any(c.get("context_id") == call.get("context_id") for call in related_calls)]
        related_contexts += [c for c in contexts if any(c.get("context_id") == call.get("context_id") for call in related_tool_calls)]
        if related_contexts:
            context_ids = sorted({c.get("context_id") for c in related_contexts if c.get("context_id")})
            context = sorted(related_contexts, key=lambda c: (c.get("created_at") or "", c.get("context_id") or ""))[-1]
            item["contextId"] = context.get("context_id")
            item["contextIds"] = context_ids
            item["contextStatus"] = context.get("status", "frozen")
            item["manifestHash"] = context.get("manifest_hash")
            item["wRevision"] = context.get("w_revision")
            item["previousChainHash"] = context.get("previous_chain_hash")
            item["chainHash"] = context.get("chain_hash")
            item["invocationStatus"] = context.get("invocation_status")
            item["invocationAttemptCount"] = context.get("attempt_count")
            item["invocationResultArtifactId"] = context.get("result_artifact_id")
            item["contextManifest"] = context.get("manifest_json") if context.get("manifest_json") else None
        artifact_ids = set(item.get("artifactIds", []))
        artifact_ids.update(a["artifact_id"] for a in artifacts if sid and a.get("step_run_id") == sid)
        for record in steps:
            if sid and record["step_run_id"] == sid:
                artifact_ids.update(output_artifact_refs(record.get("output_json")))
        for call in related_calls:
            artifact_ids.update(call[k] for k in ("input_artifact_id", "output_artifact_id") if call.get(k))
        item["artifactIds"] = sorted(artifact_ids)
        item["approvalIds"] = [a["approval_id"] for a in approvals if item.get("executionKey") and
                               a.get("step_key") == item["executionKey"]]
        item['humanGateAt'] = min((a.get('created_at') for a in approvals if a['approval_id'] in item['approvalIds']), key=_time, default=None)
        pending_approvals = [a for a in approvals if a["approval_id"] in item["approvalIds"] and a.get("status") == "pending"]
        latest_attempt = by_key.get(item.get("executionKey"), [item])[-1]["id"] == item["id"]
        if pending_approvals and latest_attempt and run.get("status") == "waiting-human":
            item["status"] = "waiting-human"
        item["pendingApprovalAt"] = min((a.get("created_at") for a in pending_approvals), key=_time, default=None)
        item['executionMethod'] = execution_method(models=item['llmCalls'], tools=item['toolCalls'],
            dispatches=item.get('dispatches', []), delegations=item.get('delegations', []),
            kind=item['kind'], observed=item.get('observed', False),
            human=item['status'] == 'waiting-human' or bool(item['approvalIds']))

    # Attach a stable phase summary to every attempt. The UI can collapse
    # retries without losing their IDs, timestamps, or individual errors.
    for key, values in by_key.items():
        ordered = sorted(values, key=lambda n: (n.get("attempt", 0), _time(n.get("createdAt"))))
        latest = ordered[-1]
        summary = {
            "executionKey": key,
            "attemptCount": len(ordered),
            "latestAttempt": latest.get("attempt", 0),
            "latestStatus": latest.get("status"),
            "failureCount": sum(1 for n in ordered if n.get("failureKind") == "execution-error"),
            "interruptionCount": sum(1 for n in ordered if n.get("failureKind") == "worker-interrupted"),
            "recoveredCount": sum(1 for n in ordered if n.get("recoveredBy")),
            "gapCount": int(latest.get("gapCount") or 0),
            "totalGapCount": sum(int(n.get("gapCount") or 0) for n in ordered),
            "latestOutputSummary": latest.get("outputSummary"),
            "latestElapsedMs": latest.get("elapsedMs"),
            "toolCallCount": sum(int(n.get("toolCallCount") or 0) for n in ordered),
            "toolFailureCount": sum(int(n.get("toolFailureCount") or 0) for n in ordered),
        }
        for n in ordered:
            n["phaseSummary"] = summary

    groups = [g for g in groups if any(n["group"] == g["id"] for n in nodes)]
    baseline = getattr(profile, "execution_baseline", {}) or {}
    return {"run": {"runId": run["run_id"], "agentKey": run.get("agent_key"), "agentName": profile.display_name,
                    "agentVersion": profile.version, "status": run.get("status"), "createdAt": run.get("created_at"),
                    "completedAt": run.get("completed_at"), "updatedAt": run.get("updated_at"),
                    "error": run.get("error_message"), "mode": "research" if research else "fixed",
                    "automationMode": automation_mode,
                    "baselineId": run.get("baseline_id") or baseline.get("baselineId"),
                    "baselineHash": run.get("baseline_hash") or baseline.get("graphHash"),
                    "baselineVersion": baseline.get("baselineVersion"),
                    "baselineStatus": baseline.get("status")},
            "nodes": nodes, "edges": edges, "groups": groups, "counts": dict(Counter(n["status"] for n in nodes)),
            "definitionEdges": [{"fromKey": dep, "toKey": key} for key, dependencies in dependency_keys.items() for dep in dependencies],
            "plans": [{"id": p["plan_id"], "version": p["plan_version"], "hash": p.get("plan_hash"),
                       "accepted": p.get("validation_json", {}).get("accepted") is True,
                       "validation": p.get("validation_json"), "createdAt": p.get("created_at")}
                      for p in plans], "warnings": list(dict.fromkeys(warnings)),
            "events": [{"id": e["event_id"], "type": e.get("event_type"), "stepKey": e.get("step_key"),
                        "status": e.get("detail_json", {}).get("status") if e.get("event_type") == "run.status" else None,
                        "createdAt": e.get("created_at")} for e in events],
            "loopRules": [{k: rule.get(k) for k in ("loop_id", "trigger_condition", "return_step_key", "max_iterations",
                                                   "no_progress_limit", "stop_action")} for rule in profile.loop_rules]}
