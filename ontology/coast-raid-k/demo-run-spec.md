# 指战本体知识库第一版完整运行 Demo

## 目标

用一个可回放的 `run-001` 验证知识库、Agent、人在回路、授权、执行和审计的完整闭环。静态知识包是依据，运行事件是 append-only 证据；任何 Agent 或大模型都不能直接写最终授权或执行结果。

## 主路径

```text
RunStarted
  -> ObservationCreated
  -> BeliefAssertionCreated
  -> DecisionDraftCreated
  -> ActionRequestCreated
  -> GatePrecheckCompleted
  -> HumanReviewRequested
  -> HumanDecisionRecorded(approved)
  -> AuthorizationIssued(allow)
  -> ActionExecutionStarted
  -> ActionExecutionCompleted
  -> EffectRecorded
  -> AuditEventRecorded
```

每个事件必须携带 `eventId`、`runId`、`seq`、`causationId`、`correlationId`、`packageId`、`packageVersion`、`modelId`、`modelRevision` 和 `provenance`。跨对象引用必须使用稳定 IRI。

## 必须演示的分支

### 人工拒绝

`ActionRequest -> HumanReviewRequested -> HumanDecisionRecorded(rejected) -> AuthorizationIssued(deny) -> AuditEventRecorded`

拒绝不能静默丢弃，也不能产生 `ActionExecution(executed)`。

### 授权过期或撤销

`AuthorizationIssued(allow) -> AuthorizationExpired|AuthorizationRevoked -> ActionExecution(aborted) -> SafeFallbackApplied -> AuditEventRecorded`

执行器必须重新检查授权状态、TTL、撤销状态、连接状态和中止条件。

### 补充证据

`HumanDecisionRecorded(request-more-evidence) -> EvidenceRequestCreated -> ObservationCreated -> BeliefAssertionCreated -> DecisionDraftRevisionCreated`

补充证据产生新的推荐/请求版本，旧版本保留且不可覆盖。

## 人在回路规则

- `in-the-loop`：没有批准事件，不能发布最终 `Authorization`。
- `on-the-loop`：允许 Gate 自动授权，但工作台必须显示活动授权、倒计时和撤销入口。
- `out-of-the-loop`：仅限低风险、局部、可逆动作；仍必须审计。
- 操作员的批准只产生 `HumanDecision`，最终 `Authorization` 仍由 Gate 服务发布。
- 操作员修改动作参数时，必须创建新的 `ActionRequest`，禁止就地修改原请求。

## 大模型边界

第一版可使用确定性 `RecommendationProvider` 生成 `DecisionDraft`。真实大模型接入后只能生成结构化推荐、备选方案和解释；输出必须通过 Schema、证据和 Gate 校验，不能直接调用 Effectors、写入 `Authorization` 或修改规则。

## 验收问题

1. 能否从 `run-001` 的 Trace 重放观察、信念、推荐、人工决定、授权、执行和效果？
2. 没有人工批准时，`in-the-loop` 请求是否必然停在 Human Queue？
3. 人工拒绝、授权过期、授权撤销和失联回退是否都有事件和证据？
4. 同一个请求被修改后，旧版本和新版本是否都可见？
5. Gate 是否是唯一产生最终 `Authorization` 的主体？
6. 大模型或推荐服务超时后，是否回退到确定性安全路径？
