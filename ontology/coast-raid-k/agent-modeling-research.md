# coast-raid-k 作战 Agent 本体建模研究与扩展提案

版本：研究草案 0.1  
范围：作战 Agent 的概念模型、运行产品、组织协同和授权边界。本文不直接改变 40ms 仿真代码，先作为 TBox、Schema 和运行契约的设计基线。

## 0. 理论基础：本体、Agent 与运行模型的边界

Neches 等人在 1991 年把本体描述为：给出领域词汇的基本术语、术语之间的关系，以及规定这些词汇外延的规则。Gruber 在 1993 年将其进一步概括为“概念化的明确规范说明”。这两种表述共同确定了本项目的建模边界：本体不是某一段 Agent 代码，也不是问答系统的提示词，而是对概念（TBox）、关系和约束（公理/形状）以及运行个体（ABox）进行可交换、可验证的规范。

因此，本项目应明确区分四层：

1. **概念化**：作战场景中有哪些 Agent、平台、组织、任务、信念、动作和授权，以及它们如何关联；
2. **规范化**：`tbox.json` 和 `agent.extension.json` 中的类、对象属性、数据属性、域/值域和不相交约束；
3. **运行断言**：`AgentInstance`、`Observation`、`BeliefAssertion`、`ActionRequest` 和 `Authorization` 等带 `runId`、有效时间和来源的个体；
4. **上下文服务**：OpenViking 或其他存储只负责按主体、权限和生命周期保存/检索这些规范与断言，不能替代本体语义或授权推理。

`BeliefAssertion` 是某个 Agent 在特定时间对命题的断言，不等于场景事实；`Decision` 是选择结果，不等于 `Action`；`Authorization` 是规则检查结果，不等于动作执行。只有保持这些层次，异质 Agent 才能在同一语义基础上交换模型而不把推测、回答或缓存误当成事实。

图片中的 OrgMAS 形式 `AgentClass = <name, R, G, MO>` 也纳入扩展：`AgentClass` 表示 Agent 类模型，`R` 映射为 `specifiesRole`，`G` 映射为 `specifiesGoal`，`MO` 映射为 `usesOrganizationModel`，而运行中的 `AgentInstance` 通过 `realizesAgentClass` 实现该类。这样角色、目标和组织模型是可复用规范，个体的生命周期、信念和动作仍属于 ABox/运行层。

## 1. 研究结论

当前包把 Agent 降格为 `ShellTemplate`、`Stage`、`Monitor` 和 `FunctionBind`。这能够描述“某类壳体读什么、写什么”，但不能描述真正的作战决策主体：谁在观察、谁持有什么信念、谁承担什么目标、谁选择计划、谁发出动作请求、谁受到组织和授权约束。

本提案采用四个互补框架：

1. **Agent 理论**：Wooldridge/Jennings 的自主性、反应性、主动性和社会能力，作为 Agent 的必要特征。
2. **BDI**：Belief、Desire/Goal、Intention/Plan，描述内部认知和承诺；信念不是事实，必须带来源、时间和置信度。
3. **OODA**：Observe、Orient、Decide、Act，描述作战运行周期；它是过程视图，不应直接替代 BDI 状态。
4. **离散事件与 Agent-based simulation**：DEVS/事件调度负责仿真时间和事件路由，ODD protocol 负责把模型目的、实体、状态、过程、调度、输入和输出写清楚。

因此，coast-raid-k 采用 **BDI + OODA + 离散事件** 的混合模型：

```text
Observation
    -> BeliefAssertion / BeliefState
    -> Goal / Task
    -> Intention / Plan / Policy
    -> Decision / ActionRequest
    -> AuthorizationResult
    -> Action / Effect
    -> Observation
```

`Permit` 只表示授权结果，不能等同于 Agent 的决策、动作或事实。

## 2. 作战建模案例给出的共性结构

公开的军事 Agent 仿真研究和工具反复出现以下分层：

- **MANA**（Map-Aware Non-uniform Automata）类军事 Agent 仿真把个体、地形/环境、局部感知、行为规则和交互放在同一框架中，用于研究行动中的涌现行为；它提醒我们必须显式建模局部观测、通信限制和环境作用，不能把全局真值直接灌给每个 Agent。当前引用的 `mana-assignment-case` DOI 是一般多 Agent 任务分配论文，不能把该论文当作 MANA 的权威军事本体来源；本提案仅将其作为交互/分配结构的旁证。
- **OneSAF 类实体级仿真**把平台、传感器、武器、任务、指挥关系和行为逻辑分开；平台是物理实体，Agent/行为模型是决策和任务执行层。
- **AFSIM 类任务级仿真**通常将平台、传感器、武器、通信、任务计划和脚本/策略分别描述，再通过事件和任务调度连接；这与本项目的 `Track → Identity → Pair → Slot → Permit` 产品链相容。
- **C2SIM/C-BML/JC3IEDM 方向**强调跨系统交换必须区分实体、任务、命令、位置、报告、时间、来源和状态，不能只交换自然语言标签。
- **无人机/防空协同类 Agent 研究**通常采用局部感知、目标识别、任务分配、编队/资源约束、通信中断和授权规则；这正对应当前的低空波次、SAM 通道、岸上链路、编队槽位和门控规则。

这些案例不能直接作为本项目的实现代码，但可作为本体边界的证据：

```text
Platform != Agent
AgentType/ShellTemplate != AgentInstance
Role != Agent
Belief != Fact
Decision != Action
Permit != Decision
Organization != CommunicationChannel
Environment/EventInjector != CombatAgent
```

## 3. 作战 Agent 的正式定义

在本项目中，`Agent` 定义为：

> 能在给定环境和运行边界内接收局部观测，维护带来源和有效时间的内部状态，形成或接受目标/任务，依据能力、策略、组织关系和授权规则选择动作，并对动作结果承担可追溯责任的决策主体。

由此：

- `Platform` 是物理承载或可控制资源，可以被一个或多个软件 Agent 控制；
- `AgentType` 是 Agent 类别和能力规范；
- `ShellTemplate` 是 Agent 架构、阶段、输入输出和状态转移的规范文档；
- `AgentInstance` 是一次运行中真正存在的主体；
- `Role` 是 Agent 在组织或任务中的依存角色；
- `Monitor` 是授权角色，可以由人、服务或专门 Agent 扮演；
- 智能助手是外部交互代理，只有在显式授予 `AgentInstance + Authority` 时才可以被建模为作战 Agent。

### 与 OpenViking 上下文主体的对应

本项目至少存在三种上下文，不能混成一个“问答上下文”：

1. **OperationalContext / SharedSituation**：战区、剧本、运行和团队共享产品。主体是 `Scenario` 或 `Run`，消费者是多个 Agent，来源可以是传感器、仿真器和规则服务。
2. **AgentLocalContext**：某个 `AgentInstance` 的局部 Observation、BeliefState、Goal、Intention、经验和可见资源。主体是 Agent，必须按 Agent 身份和权限隔离。
3. **AssistantSessionContext**：操作员与智能助手的会话。主体是 `User/Operator + AssistantSession`，只能引用前两种上下文并生成查询、解释和任务请求，不能把回答内容直接提升为作战事实、Agent 信念或 Permit。

本体上用 `AssistantSession`、`Operator`、`initiatedBy` 和 `representsAgent` 保留这条审计边界。`representsAgent` 只表示代办关系，不等于 `decidedBy` 或 `authorizedBy`；会话提交的 `ActionRequest` 仍须经过作战 Agent 的决策链和 Gate Monitor 授权。

推荐的 OpenViking 映射是：共享规范和客观知识放 `viking://resources/coast-raid-k/`，Agent 技能放 `viking://agent/skills/coast-raid-k/`，Agent 经验和私有记忆放 `viking://~/memories/coast-raid-k/`，助手会话放 `viking://~/sessions/{session_id}/`。每条上下文都应声明 `contextOwner`、`consumerAgent`、`producer`、`runId`、`visibility`、`ontologyVersion`、`validTime` 和 `provenance`。

助手可以代表某个 Agent 读取或解释上下文，但 `assistant actor`、`consumerAgent`、`assertedBy` 和 `decidedBy` 必须分别记录。OpenViking 的 account/user/peer 是存储隔离机制，不能替代项目里的 Agent stable IRI、权限和组织关系。

## 3.1 作战 Agent 划分原则与责任边界

### 3.1.1 划分依据：时间、权限和失败隔离

本项目不按“侦察兵、炮手、参谋”等拟人岗位，也不按陆海空网军种或底层模型厂商来划分 Agent。那些标签可以作为角色或实现说明，但不能成为指挥边界。作战 Agent 的第一划分依据应是三个可验证问题：**它处理哪个时间尺度的问题；它被授予哪些写入和执行权限；它失败、被欺骗或失联时，是否会把整条指挥链拖死。**

第一条硬约束是禁止一个 Agent 同时拥有“看、想、批、打”的完整权限。感知和理解可以快、可以宽；建议可以生成多个选项；授权必须窄、可审计；直接碰物理世界的动作必须可撤回、可中止。自主范围随动作的可逆性扩大或收缩，而不随模型评分或语言表达的自信程度扩大。

同一物理对象（例如一座雷达或一支编队）可以在多个时间层出现，但每层必须有独立的 `state`、`goal`、`policy`、`authorityScope` 和写入边界。秒级 Agent 不能因为局部交战收益而修改日级弹药约束或长期交战规则。跨时间层只交换版本化的事实、信念、任务和请求，并保留 `validTime`、`provenance` 与 `runId`。

### 3.1.2 四层时钟

| 时间层 | 典型问题 | Agent 可以做什么 | 明确不能做什么 |
|---|---|---|---|
| 秒至十秒 | 来袭、丢失、链路中断 | 告警、跟踪维持、本地防护建议、进入安全策略 | 改写战役目标、释放战略储备、扩大交战规则 |
| 分钟至十分钟 | 波次、窗口、弹药分配 | 方案对比、局部资源账、在既定规则内提出滚动分配 | 自行消耗战略储备、把局部最优写成全局目标 |
| 小时至日 | 战役节奏、后勤、电磁环境 | 推演、补给安排、备选轴线、跨域任务协调 | 把推演结果当作已发生事实，越权修改实时授权 |
| 周及以上 | 工业、动员、规则与舆论 | 能力缺口、可持续性、规则版本和训练数据评估 | 用单日战术胜负直接改写长期约束或红线 |

这四层是时间责任边界，不要求实现为四个进程；实现上可由同一平台上的不同 `AgentInstance` 承担，但必须使用不同的 `AgentType`/`Policy`/`Authority`，并经消息或运行产品显式交换。

### 3.1.3 七类职能 Agent

建议将作战 Agent 的核心职能收敛为七类。它们是可复用的 `AgentType`/`AgentRole`，不是七个拟人岗位；后勤、电磁、网络、民防和工业默认作为 `World` 或 `Self` 的子域，只有在物理规律和失败模式明显不同、且仍共用同一 `Gate` 时才单独升级为域。

| 类型 | 只负责什么 | 主要产出 | 硬边界 |
|---|---|---|---|
| `World` 世界模型 Agent | 根据局部/融合观测描述“现在认为发生了什么” | 带时间戳的态势、轨迹、置信度、不确定性和可能错误点 | 不作战役决心、不发 `Permit`；观测、推断和事实必须分开 |
| `Self` 蓝方自我 Agent | 维护己方弹药、传感器、链路、疲劳、损伤、误伤几何和可用能力 | `SelfState`、资源账、能力/可用性断言 | 不把“平台理论上能做”当作当前可执行能力；不替 Gate 放行 |
| `Adversary` 对手模型 Agent | 评估对方目标、欺骗、适应性和可能行动 | 对手意图假设、欺骗假设、先验和反应分支 | 不代替 `World` 断言已发生事实；不直接生成我方行动方案 |
| `Options` 方案 Agent | 在给定目标、资源和不变约束下产生可执行选项 | 方案集合、代价、窗口、失败回退、约束影响 | 无发射权、无规则改写权；成功标准是留在可接受集合，不是毁伤最大 |
| `Gate` 约束与授权 Agent | 检查识别、区分、比例、升级、禁打、友军/平民风险和权限 | `Authorization`/`Permit`、拒绝原因、证据清单 | 只作规则检查和否决，不发明打法；是唯一可否决 `Options` 的层 |
| `Effectors` 效果执行 Agent | 闭合火控、干扰、诱饵、机动、抢修等物理动作 | `Action`、执行状态、`Effect`、中止事件 | 每个实例只有窄动作集、短授权寿命和明确中止条件；失联默认安全停机 |
| `Learn` 学习与战后 Agent | 从轨迹发现误判、绕过授权和不确定性失真 | 模型评估、先验更新建议、审计报告和训练样本 | 不能直接改 `Gate` 红线、权限范围或当前运行事实 |

`World` 与 `Adversary` 必须拆开：看见了什么和对方想让我们看见什么不是同一个问题。`Self` 必须独立：很多失败来自不知道自己还能做什么，而不是没有发现目标。`Options` 只产生可比较的候选，`Gate` 只检查是否允许，`Effectors` 只执行短寿命授权。这一切共同避免“一个超级参谋包办 OODA”形成单点故障，也避免把聊天人格误当成军职。

### 3.1.4 人机责任面与可逆性

人应被建模为责任面，而不是在 Agent 图中隐藏的“最终用户”。至少区分：

- **人在环上（on the loop）**：人查看态势、选择方案并批准授权，适合分至日级的任务编排；
- **人在环中（in the loop）**：人对具体效果点头或否决，适合不可逆毁伤、身份不确定或升级风险高的动作；
- **人在环外（out of the loop）**：仅允许可逆、局部且失败代价封顶的动作，例如自卫干扰、诱饵投放和链路切换。

`HumanControlMode` 应是 `ActionRequest`/`Authority` 的显式属性，并由动作的 `reversibility`、影响范围、预计损失和识别不确定性共同决定。任何 Agent 都不能以“人不在线”作为扩大权限的理由。`Operator`、`AssistantSession` 和作战 Agent 的关系必须分别记录：助手可以代办查询或提交请求，但不能把会话回答提升为事实、信念、决心或授权。

### 3.1.5 失败隔离、撤销和安全默认值

每个 `AgentInstance` 都应声明 `failureContainment`、`safeFallbackPolicy`、`connectivityMode` 和可逆性；每个授权/执行记录还应声明 `authorizationStatus`、`expiresAt`、撤销状态和中止条件。设计评审至少检查以下不变量：

1. `World` 受骗时，错误只能先污染带来源的 `BeliefAssertion`，不能直接越过 `Options` 和 `Gate` 生成动作；
2. `Options` 生成异常或目标函数被钻空子时，不能写 `Permit` 或直接调用 `Effectors`；
3. `Gate` 无法验证证据、策略版本过期、链路可信度不足或身份不可分时，默认拒绝或收紧授权；
4. `Effectors` 的授权有短 `validUntil`、唯一 `requestId` 和中止条件，重复消息不能重放动作；
5. `Learn` 的输出只能进入待审核的模型/先验版本，不能在线修改 `Authority`、`Policy` 红线或正在执行的请求；
6. 任何 Agent 被隔离、减员或重启后，其他 Agent 只能依据可见的最后版本继续，不能隐式读取其私有信念。

“可撤”是自主边界的放大器：动作越不可逆、影响越广、失败越难隔离，越需要 `Gate` 和人在环中；动作越局部、短时、可回滚，才可以在环外运行。工程上的成功标准不是“系统仍能继续打”，而是故障时能停在已批准的安全策略，并留下可重放的 `AuditEvent`。

### 3.1.6 通信降级与上下文边界

网络默认按分区、延迟、伪造和断链设计。每个 Agent 必须声明三种工作模式及其可见上下文：

| 模式 | 可用上下文 | 允许行为 | Gate 默认策略 |
|---|---|---|---|
| `online` 在线 | 当前 `SharedSituation`、版本化团队信念和跨域方案 | 可进行跨域方案比较和经授权协同 | 按完整证据检查 |
| `degraded` 降级 | 本分区 `Self`、局部 `World`、过期但标注有效期的摘要 | 只允许本地可验证、影响受限的动作；收缩 `Options` | 收紧阈值和作用范围 |
| `disconnected` 失联 | 本地缓存、最后批准的短策略和自身安全状态 | `Effectors` 仅执行短寿命预批准策略，超时转安全状态 | 默认拒绝新授权，禁止扩大权限 |

断链时不能把“最后一次收到的命令”当作永久意图，也不能把过期共享信念当成当前事实。`LinkState`、`ContextScope`、`staleness`、`visibility` 和 `lastKnownAt` 应随每条消息/断言传递。评估划分是否合理的关键演练是：**通信中断十分钟后，系统是否仍只执行局部、可验证和可撤销动作，而不会自行发明新的战争目标。**

### 3.1.7 映射到 coast-raid-k

现有运行 Agent 可按上述职能重新解释，而不必按军种重建：

| 现有实例 | 建议主类型 | 允许产出 | 禁止越过的边界 |
|---|---|---|---|
| `agent.sensor.shore`、`agent.sensor.ship` | `World`（局部传感子域） | `Observation`、局部 `Track`、不确定性 | 不直接产生身份结论或 `Permit` |
| `agent.fusion.c2` | `World` + 受限 `Adversary` 融合服务 | `BeliefAssertion`、身份候选、欺骗假设 | 不直接选择或执行交战动作 |
| `agent.assign.sam` | `Options` | `Task`、`Plan`、`Pair`、备选分配及回退 | 无发射权、无授权写权 |
| `agent.form.blue` | `Options`/局部 `Effectors` | 编队 `ActionRequest`、槽位调整和中止请求 | 只执行已授权的几何/机动动作 |
| `agent.monitor.gate` | `Gate` | `Authorization`/`Permit`、检查证据和拒绝原因 | 不替方案 Agent 发明打法，不执行物理动作 |
| `agent.inject.scenario` | 环境/实验控制器 | `Wave`、`Jam`、`Attrition`、环境注入 | 不应被误认为战斗 Agent 或授权主体 |
| 新增 `agent.self.status` | `Self` | 资源、平台、链路和损伤状态 | 不把能力估计直接变成行动命令 |
| 新增 `agent.learn.audit` | `Learn` + 解释审计 | `AuditEvent`、模型评估、待审核更新 | 不在线改写 Gate 红线 |

因此，现有 `Track → Identity → Pair → Slot → Permit` 产品链应改写为带责任主体和权限边界的链：

```text
World/Self/Adversary
  -> BeliefAssertion
  -> Goal/Task
  -> Options: Plan / Decision / ActionRequest
  -> Gate: Authorization
  -> Effectors: Action / Effect
  -> Learn: AuditEvent / update proposal
```

这条链保留现有几何、波次和泡的模型，但把“谁可以写入哪一种产品”变成可验证的本体约束。当前扩展已经落地 `AgentFunction`、`OperationalTimeScale`、`functionDomain`、`timeScale`、`humanControlMode`、`connectivityMode`、`failureContainment`、`safeFallbackPolicy` 和 `reversibility`，并把授权寿命、撤销状态、证据、中止条件和执行安全边界写入 JSON Schema 与 SHACL。`ContextScope`、消息新鲜度和完整的 LinkState 可见性仍是后续运行时工作；本节不把这些未完成部分标记为已完成。

### 3.1.8 当前实现状态与验证入口

实现采用“职责投影 + 正式继承分离”的方式：七类功能域以 `functionDomain` 的受控值表示，正式类层级仍由 `rdfs:subClassOf` 保持；`performsFunction` 和 `operatesAtTimeScale` 为图交换保留了可扩展的对象属性。运行实例必须声明时间尺度、人机控制模式、连接模式、失败隔离边界、安全回退和可逆性。

静态约束已覆盖三条硬边界：只有声明 `Gate` 功能的主体可以产生 `Authorization`；`ActionExecution` 必须引用授权、到期时间、中止条件和安全回退；`Learn` 不能直接写入 `Policy`、`Authority` 或 `Authorization`。`result` 仍表示一次裁决（`allow`、`deny`、`indeterminate`），`authorizationStatus` 单独表示 `active`、`expired` 或 `revoked`，避免把生命周期状态误当成决策结果。

浏览器知识库的 Agent 导航是上述本体的责任视图，不是新的继承层级；打开 `AgentInstance` 后，首个“责任边界”页签可检查这些字段。十项 competency questions 面板只把可由当前包验证的内容标为“已落实”，运行时重放、完整 LinkState 投影和任务重分配仍显示为“部分落实”或“待补齐”。

## 4. 建议新增的 TBox 类

### 4.1 实体与组织层

| 类 | 上位类 | 用途 |
|---|---|---|
| `Agent` | `IndependentContinuant` | Agent 的抽象决策主体 |
| `AgentInstance` | `Agent` | 某次运行中的具体 Agent |
| `AgentType` | `InformationEntity` | Agent 类型/架构规范 |
| `Organization` | `IndependentContinuant` | 指挥组织、编队、团队 |
| `Team` | `Organization` | 任务团队或编组 |
| `CommandEchelon` | `Organization` | 指挥层级 |
| `AgentRole` | `Role` | 传感、融合、分配、执行、授权等角色 |
| `RoleAssignment` | `Relator` | Agent、Role、Organization、Task 的时态绑定 |
| `PlatformBinding` | `Relator` | Agent 与物理平台的绑定 |

### 4.2 认知与决策层

| 类 | 上位类 | 用途 |
|---|---|---|
| `Observation` | `InformationEntity` | Agent 获得的局部观测 |
| `BeliefState` | `InformationEntity` | Agent 当前内部信念状态 |
| `BeliefAssertion` | `InformationEntity` | 关于某实体/命题的一条带来源信念 |
| `Goal` | `InformationEntity` | 期望达到的状态 |
| `Desire` | `Goal` | 可选的动机集合 |
| `Intention` | `InformationEntity` | Agent 已承诺执行的目标/计划 |
| `Task` | `Process` | 可分配、可执行的任务 |
| `Plan` | `InformationEntity` | 达成任务的步骤和选择条件 |
| `Policy` | `InformationEntity` | 决策、权限和行为约束 |
| `Decision` | `InformationEntity` | 某 Agent 在特定证据下作出的选择 |
| `ActionRequest` | `InformationEntity` | 对动作执行或授权的请求 |

### 4.3 行为、通信和治理层

| 类 | 上位类 | 用途 |
|---|---|---|
| `Action` | `Process` | Agent 或平台执行的动作 |
| `Effect` | `InformationEntity` | 动作造成的状态变化或结果 |
| `StateTransition` | `Process` | 状态转换及其前置/后置条件 |
| `Message` | `InformationEntity` | Agent 间交换的消息载荷 |
| `CommunicationAct` | `Process` | 发送、接收、确认、拒绝等通信行为 |
| `Protocol` | `InformationEntity` | 通信或协同协议 |
| `LinkState` | `Quality` | 链路的通断、延迟、带宽和可见性 |
| `Capability` | `Disposition` | Agent 可执行或可提供的能力 |
| `Resource` | `IndependentContinuant` | 弹药、通道、能量、传感时间等资源 |
| `Constraint` | `InformationEntity` | 几何、数量、时间、通信和安全约束 |
| `Authority` | `InformationEntity` | Agent 的权限范围 |
| `Authorization` | `InformationEntity` | 对某个动作请求的授权结果 |
| `AuditEvent` | `Occurrent` | 运行过程中的审计事件 |
| `Run` | `Occurrent` | 一次仿真、演训或任务运行 |

现有类的调整：

- `ShellTemplate` 保留，但上位含义改为 Agent 架构规范；
- `Stage` 改为 `ProcessSpecification` 或 `StateTransition` 的集合，不再假定五个阶段本身就是 Agent；
- `Monitor` 保留为 `AgentRole` 的一个实例类型；
- `Permit` 改为 `AuthorizationResult` 的特化，仍然只能由 Gate Monitor 产生；
- `Track`、`Identity`、`Pair`、`Slot` 是运行产品，不是 Agent 的内在属性；
- `PlatformType.tasks/role/intent` 中的任务和角色迁移到 Agent 任务/角色绑定，平台型谱只保留物理性能和可承载能力。

## 5. 建议新增的对象属性

```text
realizesType(AgentInstance, AgentType)
usesTemplate(AgentInstance, ShellTemplate)
implementedOn(AgentInstance, Platform)
hasRole(AgentInstance, AgentRole)
hasAssignment(AgentInstance, RoleAssignment)
memberOf(AgentInstance, Organization)
reportsTo(AgentInstance, AgentInstance)
commands(AgentInstance, AgentInstance)
hasCapability(AgentInstance, Capability)
usesResource(AgentInstance, Resource)
observes(AgentInstance, Observation)
updates(Observation, BeliefState)
asserts(BeliefState, BeliefAssertion)
believesAbout(BeliefAssertion, Entity)
adoptsGoal(AgentInstance, Goal)
assignedTask(AgentInstance, Task)
formsIntention(AgentInstance, Intention)
selectsPlan(Intention, Plan)
governedBy(AgentInstance, Policy)
requestsAction(AgentInstance, ActionRequest)
authorizedBy(ActionRequest, Authorization)
executes(AgentInstance, Action)
realizes(Action, ActionRequest)
produces(Action, Effect)
sends(AgentInstance, Message)
receives(AgentInstance, Message)
usesProtocol(CommunicationAct, Protocol)
hasLinkState(AgentInstance, LinkState)
derivedFrom(InformationEntity, InformationEntity)
assertedBy(InformationEntity, AgentInstance)
validDuring(InformationEntity, TimeInterval)
withinRun(InformationEntity, Run)
```

已有 `reads`/`writes` 可以保留为一般信息访问关系，但不能替代上面的认知和行为关系。尤其 `writes(Permit)` 只是写入权限，不能说明 Agent 已经作出决策或实际执行动作。

## 6. 建议新增的数据属性

### AgentInstance

```text
stableIri
agentId
agentTypeId
ownerId
organizationId
side
autonomyLevel
decisionMode             # reactive | bdi | rule | human-in-loop | hybrid
lifecycleState           # initialized | active | degraded | suspended | destroyed
authorityScope
modelId
modelRevision
validFrom
validTo
```

### Observation / BeliefAssertion

```text
observedAt
sourceIri
sensorIri
frame
unit
latencyMs
confidence
probability
uncertaintyModel
fusionMethod
classification
validFrom
validTo
```

### Goal / Task / Plan / Action

```text
priority
precondition
postcondition
deadline
resourceBudget
policyVersion
planRevision
actionType
status
failureReason
```

这些字段必须区分“事实值”“Agent 信念”“推理结果”和“授权结果”，不能都使用一个 `p` 或一个 `state` 字段。

## 7. Agent 运行闭环

一个最小作战 Agent 实例必须能够表示以下闭环：

```text
AgentInstance
  observes Observation
  updates BeliefState
  adopts Goal / receives Task
  forms Intention
  selects Plan under Policy
  creates Decision / ActionRequest
  requests Authorization when required
  executes Action
  produces Effect / AuditEvent
  receives new Observation
```

在当前海岸场景中，可以先定义这些 Agent：

| Agent 实例 | 主要职责 | 读取 | 产生 |
|---|---|---|---|
| `agent.sensor.shore` | 岸搜感知 | `SEARCH`、局部回波 | `Observation`、`Track` |
| `agent.sensor.ship` | 舰载感知 | `B_ship` | `Observation`、局部 `Track` |
| `agent.fusion.c2` | 融合和身份判断 | 多源 `Track`、`LinkState` | `BeliefAssertion`、`Identity` |
| `agent.assign.sam` | 任务分配和配对 | `Identity`、`Channel` | `Pair`、`Task` |
| `agent.form.blue` | 编队和槽位控制 | `Pair`、`Slot`、走廊约束 | `ActionRequest`、`Slot` |
| `agent.monitor.gate` | 规则授权 | `Identity`、位置、链路、通道、ROE | `Permit`/`Authorization` |
| `agent.inject.scenario` | 环境注入 | 运行时钟 | `Wave`、`Inject`、`Jam`、`Attrition` |

`agent.inject.scenario` 是环境/实验控制主体，不应被误认为战斗 Agent；`agent.monitor.gate` 可以是服务 Agent，但它的权限来自 `Authority` 和 `Policy`，不是来自语言模型。

## 8. 对现有项目文件的映射

| 当前文件/代码 | 当前含义 | 扩展后含义 |
|---|---|---|
| `schema.agent.json` | 只有 Stage/ShellTemplate/ShellCatalog | 增加 AgentType、AgentInstance、Capability、Goal、Task、Belief、Plan、Action、Message、Organization、Authorization |
| `shells.json` | `shell-blue-uav` 模板 | 保留为 AgentType/ShellTemplate；增加架构、状态转移、输入输出、策略、失败模式 |
| `platform/battle.js makeAgent()` | 内存平台对象 | 生成带 stable IRI、AgentType、PlatformBinding、Role、Run 的 AgentInstance |
| `platform/knowledge.js CloudKB` | 云参数和门控辅助 | 拆为 BeliefState/BeliefUpdateService；保存来源、算法、时间和置信度 |
| `reactBlue` / `finite_wave_assign` | 行为函数 | `TaskAllocator`、`Plan`、`Decision` 和 `ActionRequest` |
| `pullToSlots` | 几何编队控制 | `FormationAction` 及其 Effect |
| `mayGate()` | 直接布尔门控 | `GateMonitor` 的 AuthorizationPolicy 评估器 |
| `state.events` | UI 文本事件 | `AuditEvent`/`RuntimeEnvelope`，带 actor、subject、producer、consumer、run_id |
| `schema.engagement.json` | 产品对象 | 所有 Agent 引用改为 `AgentRef`，补充 `assertedBy`、`derivedBy`、`validTime` 和来源 |
| `dictionary.json` | Agent 导航仅三张表 | 增加 AgentInstance、AgentRoleAssignment、Capability、Goal、Belief、Plan、Action、Message、Organization、Authorization |
| `validate.py` | 仅检查 ShellTemplate | 增加 Agent 闭环、IRI、生命周期、权限、来源和负例检查 |

## 9. 建议的 AgentInstance 最小 JSON

```json
{
  "type": "AgentInstance",
  "id": "agent.monitor.gate",
  "iri": "https://local.ontology/coast-raid-k/1.0.0#agent-monitor-gate",
  "agentType": "https://local.ontology/coast-raid-k/1.0.0#gate-monitor",
  "implementedOn": "https://local.ontology/coast-raid-k/1.0.0#C2",
  "organization": "https://local.ontology/coast-raid-k/1.0.0#blue-c2",
  "roles": ["https://local.ontology/coast-raid-k/1.0.0#monitor"],
  "decisionMode": "rule",
  "autonomyLevel": "supervised",
  "lifecycleState": "active",
  "authorityScope": ["https://local.ontology/coast-raid-k/1.0.0#rule-gate"],
  "policyVersion": "1.0.0",
  "modelId": "gate-service",
  "modelRevision": "gate-v1",
  "runId": "run-20260923-001",
  "validTime": {"from": 0, "to": null},
  "provenance": {"source": "shells.json", "hashStatus": "not-computed"}
}
```

一个平台 Agent 还必须增加 `implementedOn`、`hasCapability`、`observes`、`hasGoal`、`assignedTask` 和 `executes`；不能只在 `Slot.agent` 中放一段字符串。

## 10. Permit 的严格边界

```text
Agent decision        = 某 Agent 选择了一个行动方案
ActionRequest         = 请求执行某个行动
Authorization         = 按策略检查行动是否合法
Permit                = Authorization 的项目特化结果
Action                = 实际执行
Effect                = 执行造成的状态变化
```

普通平台 Agent、编队 Agent、语言模型和 OpenViking 检索结果都不能直接写 `Permit.ok=true`。只有 `agent.monitor.gate` 或同等授权服务可以产生 Permit，并且必须记录：

```text
ruleVersion
policyVersion
checks.in_zone
checks.belief_ge_tau
checks.ground_link_up
checks.channel_available
checks.identity_separable
evidenceUris
assertedBy
```

## 11. 能力问题与验证

扩展完成后，至少应能回答以下 competency questions：

1. 当前运行中有哪些 AgentInstance？每个 Agent 的 stable IRI、平台、组织、角色和生命周期是什么？
2. 某个 Agent 观察到了哪些 Observation？这些 Observation 来自哪个传感器、采用哪个算法、何时有效？
3. 某个 Identity 是事实、信念还是推理结果？它的来源、置信度和融合算法是什么？
4. 某个 Task 是谁分配的？由哪个 Goal、Plan 和 Policy 支持？
5. 某个 ActionRequest 是谁提出的？是否需要 Permit？最终由谁授权？
6. 某个 Permit 的每个门控条件是否都有可验证证据？
7. 通信中断后，哪些 Agent 仍可使用共享信念，哪些只能使用局部信念？
8. 一个 Agent 减员或失联后，哪些 Task、Pair、Slot 和 Resource 需要重新分配？
9. `browser-3d` 和 `sim-2d` 中同一目标的产品是否属于不同 `run_id/model_id`？
10. 一次运行结束后，是否能从 AuditEvent 重放 Agent 的观察、信念、任务、授权和动作链？

对应测试应包含：

- AgentInstance 缺 stable IRI、AgentType、lifecycleState 时拒绝；
- Platform 不能自动被推断成 Agent；
- ShellTemplate 不能写 Permit；
- 未经授权的 ActionRequest 不能生成 Permit；
- `不明` Identity 不能生成可交战 Permit；
- Belief 缺 source、validTime 或 confidence 时拒绝；
- 2D/3D 产品混用 run_id 时拒绝；
- 通信链路断开时，禁止 Agent 使用不可见的团队信念。

## 12. 实施顺序

1. 采用新增的 `agent.extension.json`、`schema.agent.extension.json` 和 `agent.instances.json` 作为 additive Agent TBox/交换草案，先不破坏现有 SoR；正式发布时再纳入 `catalog.json`、`dictionary.json` 和合并 Schema。
2. 将 `shell-blue-uav` 重命名为 AgentType/ShellTemplate，并补 architecture、transition、policy、capability、failureMode。
3. 为 `makeAgent()` 生成 AgentInstance 和 Run 元数据。
4. 把 CloudKB 拆出 BeliefState/BeliefAssertion/Observation provenance。
5. 把 `reactBlue`、波次分配、槽位控制转换成 Task/Plan/Decision/ActionRequest。
6. 将 `mayGate()` 替换为 Gate Monitor Authorization service。
7. 增加 Message、CommunicationAct、Organization、RoleAssignment 和 LinkState。
8. 更新 JSON Schema、SHACL、Turtle、dictionary、SQLite/PostgreSQL projection 和 validator。
9. 最后再把静态本体、技能、运行摘要投影到 OpenViking；OpenViking 只做检索和记忆，不执行 Agent 本体推理和 Permit 授权。

## 13. 参考资料与公开案例

- Neches et al., *Enabling Technology for Knowledge Sharing*（AI Magazine, 1991；术语、关系和外延规则的本体定义）：https://doi.org/10.1609/aimag.v12i3.902
- Gruber, *A Translation Approach to Portable Ontology Specifications*（Knowledge Acquisition, 1993；“概念化的明确规范说明”）：https://doi.org/10.1006/knac.1993.1008
- Wooldridge & Jennings, *Intelligent Agents: Theory and Practice*：https://doi.org/10.1017/S0269888900008122
- Jennings, Sycara & Wooldridge, *A Roadmap of Agent Research and Development*：https://doi.org/10.1016/S0004-3702(98)00058-1
- Rao & Georgeff, *The Belief-Desire-Intention Model of Agency*：https://doi.org/10.1007/3-540-49057-4_1
- Rao & Georgeff, *The BDI Model*：https://doi.org/10.7551/mitpress/5804.003.0004
- Grimm et al., ODD protocol：https://doi.org/10.1016/j.ecolmodel.2010.08.019
- Zeigler et al., DEVS introduction：https://doi.org/10.1201/9781420053371.ch2
- Boyd OODA Loop reference entry（用于过程视图；OODA 不替代 BDI）：https://doi.org/10.4135/9781452229805.n473
- *Combat Modeling Using the DEVS Formalism*：https://doi.org/10.1002/9781118180310.ch21
- *Military Applications of Agent-Based Simulations*：https://doi.org/10.1109/wsc.2004.1371314
- *MANA: A New Multi-agent Approach for Complex Assignment Problems*：https://doi.org/10.1109/computationworld.2009.31
- C2SIM 项目资料（George Mason University C5I Center 归档）：https://github.com/gmuc4i/c2sim
- C2SIM 最新项目入口：https://openc2sim.github.io/
