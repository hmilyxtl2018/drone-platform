# 作战 Agent 功能边界与安全运行本体提案

版本：0.1（设计提案）
适用包：`coast-raid-k/agent`

## 1. 结论

作战 Agent 不按“侦察兵、炮手、参谋”等拟人岗位建模，而按四个正交维度切分：

1. **功能域**：World、Self、Adversary、Options、Gate、Effectors、Learn；
2. **时间尺度**：Immediate、Tactical、Operational、Strategic；
3. **权限边界**：人机控制模式、可作用资源、授权寿命和撤销条件；
4. **故障边界**：Agent 出错时可影响的范围、降级路径和安全状态。

这四个维度不能互相替代。一个 Agent 可以同时承担 World + Tactical，但不能因为“是 World Agent”就自动获得 Action 或 Permit 权限。

现有 `agent.extension.json` 保留 `Agent`、`AgentInstance`、`Observation`、`BeliefAssertion`、`Decision`、`ActionRequest`、`Authorization`、`ActionExecution`、`AuditEvent` 等闭环骨架，并已补上功能域、时间尺度、授权寿命、可逆性、失败隔离和安全回退字段。仍需在后续运行时中继续补齐 `ContextScope`、消息新鲜度、完整 `LinkState` 可见性和持久化重放；这些运行能力不能由 OpenViking 检索或静态 Schema 自动推导。

## 2. 七类功能域

### 2.1 建议新增类

| 类 | 上位类 | 语义 |
|---|---|---|
| `AgentFunction` | `InformationEntity` | Agent 在运行中承担的功能类别，不等同于人类岗位 |
| `WorldModelAgent` | `Agent` | 维护对外部环境的观测、假设和不确定性 |
| `SelfStateAgent` | `Agent` | 维护己方资源、平台、人员、链路和损伤状态 |
| `AdversaryModelAgent` | `Agent` | 维护对手目标、欺骗、适应和可能行动的模型 |
| `OptionsAgent` | `Agent` | 产生可执行方案、代价、窗口和失败回退 |
| `GateAgent` | `Agent` | 对方案/动作按规则进行允许、否决或不确定判定 |
| `EffectorAgent` | `Agent` | 在窄动作集和短授权内执行物理或网络效果 |
| `LearningAgent` | `Agent` | 从轨迹和审计事件更新模型先验，不能直接改 Gate 红线 |
| `ExplanationAuditAgent` | `Agent` | 生成可复核解释并审计事件链，不拥有交战执行权 |

实现上应以“七类作为 `AgentFunction` 的受控实例”为主，通过 `performsFunction` 组合功能。这样同一运行实例可以承担 World + Self，而不会因为类型互斥造成冲突。

如需机器验证硬边界，再将 `WorldModelRole`、`GateRole` 等定义为 `AgentRole` 的子类，并以 SHACL 约束其可产生/禁止的产品；不要把七类直接建成 Agent 的互斥子类。`ExplanationAuditAgent` 是横向审计能力，不应被塞进 Learn 的更新闭环。

### 2.2 必须添加的关系

```text
performsFunction(AgentInstance, AgentFunction)
producesModelUpdate(LearningAgent, WorldModel | AdversaryModel | OptionsLibrary)
proposesPolicyChange(LearningAgent, Policy)
validatedBy(PolicyChange, Human | GateAuthority)
mayVeto(GateAgent, ActionRequest | Plan)
producesOption(OptionsAgent, Plan | OptionSet)
executes(EffectorAgent, Action)
explains(ExplanationAuditAgent, Decision | Authorization | ActionExecution)
audits(ExplanationAuditAgent, AuditEvent)
```

其中 `proposesPolicyChange` 只能表达“提出”，不能表达“写入”。禁止把 `LearningAgent` 的 `writes` 关系解释成可直接修改 `Policy`、`Authority` 或 `Authorization`。

### 2.3 七类的输入输出约束

| 功能域 | 允许读取 | 允许产生 | 禁止拥有 |
|---|---|---|---|
| World | 传感器、消息、环境事件 | Observation、BeliefAssertion、WorldModel | Action、Permit |
| Self | 平台、资源、链路、损伤、人员状态 | SelfState、Availability、Constraint | 敌情真值、Permit |
| Adversary | World 观测、历史轨迹、欺骗证据 | AdversaryHypothesis、Prediction | 我方授权、实际动作 |
| Options | World/Self/Adversary、任务与约束 | Plan、OptionSet、DecisionDraft | Permit、物理执行 |
| Gate | ActionRequest、Policy、Authority、证据 | Authorization、Deny、Indeterminate | 发明方案、直接执行 |
| Effectors | 已授权 Action、局部状态、AbortCondition | ActionExecution、Effect、AuditEvent | 延长授权、改规则 |
| Learn | Observation、Decision、Execution、AuditEvent | 模型版本或更新提案 | 直接改红线、直接发射 |

“输入/输出约束”建议落到 SHACL 或校验器，而不只写在 comment 中。

## 3. 四层时间尺度

新增类和受控值：

```text
OperationalTimeScale (InformationEntity)
  - Immediate      # 秒–十秒
  - Tactical       # 分–十分
  - Operational    # 小时–日
  - Strategic      # 周以上
```

为 Agent 类型和任务增加：

```text
operatesAtTimeScale(AgentType | Task, OperationalTimeScale)
hasDecisionLatencyBudget(AgentType | Task, Duration)
hasPlanningHorizon(AgentType | Task, Duration)
```

建议的默认边界：

| 尺度 | 适合问题 | 默认禁止 |
|---|---|---|
| Immediate | 来袭、丢失、链路中断、本地防护 | 改战役目标、动用战略储备 |
| Tactical | 波次、窗口、局部资源分配 | 自行改变战略储备和交战规则 |
| Operational | 战役节奏、后勤、电磁环境 | 把推演结果当作已发生事实 |
| Strategic | 工业、动员、规则和长期能力 | 用今日战术结果改写长期约束 |

同一平台/对象可在多层出现，但必须使用不同的 `AgentInstance` 或 `DecisionContext`，至少区分：

```text
clock
validTime
objectiveScope
writeAuthority
runId
```

不能用一个 `AgentState` 字段同时承载秒级交战状态和周级工业规划状态。

## 4. 权限、可逆性和人机控制

### 4.1 建议新增类

| 类 | 上位类 | 语义 |
|---|---|---|
| `HumanControlMode` | `InformationEntity` | `InTheLoop`、`OnTheLoop`、`OutOfTheLoop` |
| `ReversibilityProfile` | `Quality` | 动作的可逆程度、恢复时间和最大损失 |
| `AbortCondition` | `InformationEntity` | 必须中止执行的条件 |
| `SafeFallbackPolicy` | `Policy` | 失联、超时、证据不足时的安全动作 |
| `AuthorizationGrant` | `Authorization` | 有限资源、有限时间、可撤销的授权令牌 |
| `RevocationEvent` | `AuditEvent` | 授权被撤销或自然过期的记录 |
| `FailureContainmentBoundary` | `Quality` | Agent 失效最多可影响的范围 |

最小可运行实现先把上述控制面收敛到 `AgentInstance`、`Authorization` 和 `ActionExecution` 的受控字段；本体中已正式加入 `AgentFunction` 与 `OperationalTimeScale`，其余概念类可在需要单独表达恢复时间、撤销事件或上下文范围时再拆分，避免为了导航堆叠互斥的 Agent 子类。

受控值建议：

```text
HumanControlMode: in-the-loop | on-the-loop | out-of-the-loop
ReversibilityProfile: reversible | recoverable | irreversible
FailureContainmentBoundary: local-action | team | operational | strategic
Authorization.result: allow | deny | indeterminate | expired | revoked
```

### 4.2 关系和数据属性

```text
hasHumanControlMode(AgentType | ActionType, HumanControlMode)
hasReversibility(Action | ActionType, ReversibilityProfile)
requiresAbortCondition(ActionRequest | Authorization, AbortCondition)
fallsBackTo(AgentInstance | ActionExecution, SafeFallbackPolicy)
scopedTo(AuthorizationGrant, Action | Resource | SpatialRegion)
expiresAt(AuthorizationGrant, dateTime)
revokedBy(AuthorizationGrant, RevocationEvent)
containedBy(AgentInstance | ActionExecution, FailureContainmentBoundary)
```

`Authorization` 必须增加：

```text
scope
issuedAt
expiresAt
revocable
abortConditions
safeFallbackPolicy
requiredHumanControlMode
reversibilityClass
failureContainmentBoundary
```

授权判定必须同时检查：请求者、动作类型、目标、空间、资源、有效时间、证据、规则版本、策略版本和撤销状态。`allow` 不是永久许可，也不是 Action 已执行的事实。

### 4.3 必须写入的反向约束

```text
Authorization.result = allow
  -> requestIri 存在
  -> decidedBy 必须是 GateAgent 或具备 Gate 角色的主体
  -> expiresAt > issuedAt
  -> evidenceUris 至少一条
  -> authorization scope 不得超出 decidedBy 的 Authority

ActionExecution
  -> 必须引用未过期且未撤销的 AuthorizationGrant
  -> 必须引用 AbortCondition 和 SafeFallbackPolicy
```

## 5. 通信降级和上下文边界

新增类：

```text
ConnectivityMode (Quality)
  - online
  - degraded
  - disconnected
LocalContextPolicy (Policy)
DegradedModePolicy (Policy)
```

新增关系：

```text
hasConnectivityMode(AgentInstance, ConnectivityMode)
usesContext(AgentInstance, OperationalContext | AgentLocalContext)
fallsBackToContext(AgentInstance, AgentLocalContext)
requiresLinkState(ActionRequest, LinkState)
usesDegradedPolicy(AgentInstance, DegradedModePolicy)
```

三种模式的本体约束：

| 模式 | 可用上下文 | 允许的选项 | 默认 Gate 行为 |
|---|---|---|---|
| online | 全局 World + Self + 授权共享上下文 | 可跨域方案 | 按正常策略判定 |
| degraded | 本分区 Self + 过期 World | 收缩到本地可验证动作 | 收紧 |
| disconnected | 本地状态 + 事先批准的短寿命策略 | 仅预批准、可撤销动作 | 默认拒绝新授权 |

`Effectors` 在失联时只能执行已经批准且未过期的短策略；不能因为失联而继续沿用“最后意图”或自动扩大权限。

OpenViking 的存储账户、用户或 peer 只是上下文隔离机制，不能替代 `AgentInstance`、`Authority`、`Authorization` 和 `Run` 的本体约束。

## 6. 对现有类和关系的修改

### 6.1 保留但收紧

- `Agent`：保留为决策主体；增加 `performsFunction`、`operatesAtTimeScale`、`containedBy`。
- `AgentInstance`：必须有稳定 IRI、`AgentType`、`Run`、生命周期、功能域、故障边界和连接模式。
- `Observation` / `BeliefAssertion`：保留；强制来源、有效时间、置信度和不确定性模型。
- `Decision`：保留；增加时间尺度、选项集、失败回退和决策者。
- `ActionRequest`：保留；必须明确是否需要授权、作用范围、可逆性和中止条件。
- `Authorization`：保留；变为短寿命、可撤销、带证据和范围的判定记录。
- `ActionExecution`：保留；必须引用 AuthorizationGrant，不能由 `Decision` 直接跳到执行。
- `AuditEvent`：保留；增加 actor、subject、producer、consumer、run、modelRevision、policyVersion。

### 6.2 不建议继续扩张的关系

现有 `writes`、`reads`、`produces`、`usesResource` 是通用关系，不能单独表示“建议”“授权”“执行”。对下面几类关系，应以专用关系替代解释：

```text
writes(Agent, Policy)       -> proposesPolicyChange(LearningAgent, PolicyChange)
writes(Agent, Permit)       -> produces( GateAgent, Authorization )
produces(Agent, Action)     -> executes(EffectorAgent, Action)
representsAgent(Session, Agent) -> 不能推出 decidedBy 或 authorizedBy
```

### 6.3 建议废弃或迁移的建模方式

- 用“侦察兵/炮手/参谋”作为 Agent 的顶层分类；迁移为 `AgentFunction` 实例或 `AgentRole` 子类。
- 把一个 Agent 同时标记为 World、Options、Gate、Effectors 且没有权限边界；迁移为多个 AgentInstance 或显式的多功能组合，并为每个功能分别声明作用范围。
- 把 `Permit.ok=true` 当作事实；迁移为 `Authorization.result=allow`，并补充过期、撤销和证据。
- 把 `Stage` 当作 Agent 的内部人格/岗位；迁移为 `OperationalTimeScale`、`ProcessSpecification` 或 `StateTransition`。
- 把学习结果直接写入 Gate Policy；迁移为带版本的 `PolicyChangeProposal`，经人工或 GateAuthority 验证后发布。

## 7. 当前项目的最小 Agent 集合

建议先将已有实例明确成以下功能域，不必一次性新增大量平台：

| 实例 | 功能域 | 时间尺度 | 权限 |
|---|---|---|---|
| `agent.sensor.shore` | World | Immediate/Tactical | 观测和跟踪，不得授权 |
| `agent.sensor.ship` | World + Self | Immediate | 局部观测和平台状态 |
| `agent.fusion.c2` | World | Tactical | 融合和身份断言，不得执行 |
| `agent.self.readiness` | Self | Immediate/Tactical | 资源与链路状态，只读或报告 |
| `agent.adversary.model` | Adversary | Tactical/Operational | 假设和预测，不得改我方规则 |
| `agent.options.planner` | Options | Tactical/Operational | 方案和回退，不得 Permit |
| `agent.monitor.gate` | Gate | Immediate/Tactical | 仅规则评估、允许/否决 |
| `agent.effectors.local` | Effectors | Immediate | 极窄动作集、短寿命授权、可撤销 |
| `agent.learn.audit` | Learn + ExplanationAudit | Operational/Strategic | 模型更新提案和审计，不得改红线 |

`agent.inject.scenario` 应被声明为 `EnvironmentController` 或 `ExperimentController`，不能混入作战 Agent 总数。

## 8. 验收问题

1. 任一 Agent 是否能回答自己的功能域、时间尺度、权限范围、连接模式和故障边界？
2. World/Self/Adversary 的输出是否明确区分 Observation、SelfState 和 Hypothesis？
3. Options 是否只产生方案，Gate 是否是唯一否决层？
4. Effectors 是否只能执行仍在有效期内的 AuthorizationGrant？
5. 失联十分钟后，系统是否自动收紧权限并进入 SafeFallbackPolicy？
6. Learn 是否只能产生模型或策略变更提案，不能直接写 Gate 红线？
7. 是否能从 AuditEvent 重放“观测 → 信念 → 方案 → 授权 → 执行 → 效果”？
8. 同一平台跨四个时间尺度时，是否具有分离的状态、目标和写入权限？

## 9. 实施优先级

1. 先增加 `AgentFunction`、`OperationalTimeScale`、`HumanControlMode`、`ReversibilityProfile`、`ConnectivityMode`、`FailureContainmentBoundary` 和对应受控值。
2. 再为 `AgentInstance`、`AgentType`、`ActionRequest`、`Authorization`、`ActionExecution` 增加必填字段和 SHACL 约束。
3. 把现有 `agent.fusion.c2`、`agent.monitor.gate` 迁移到上述功能域；补 `Self`、`Adversary`、`Options`、`Effectors`、`Learn` 最小实例。
4. 把 `mayGate()` 的布尔结果投影成 Authorization 记录，加入证据、策略版本、过期和撤销。
5. 将离线/降级逻辑接入 `LinkState` 和 `SafeFallbackPolicy`；默认行为是收紧权限。
6. 最后再将 TBox、技能、Agent 私有记忆和助手会话投影到 OpenViking；OpenViking 负责检索，不负责 Gate 判定。
