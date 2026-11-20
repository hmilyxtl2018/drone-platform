# OpenViking 与 coast-raid-k 的集成边界

OpenViking 是上下文数据库和目录式检索层。它保存上下文、摘要、经验和检索轨迹，但不替代作战本体、规则引擎、仿真状态机或授权服务。项目中的 stable IRI、`runId`、`AgentInstance`、`BeliefAssertion` 和 `Authorization` 仍由本体运行时负责。

## 先回答“上下文主体是谁”

作战运行中存在三种主体，必须分开：

1. **共享作战上下文**的主体是 `Run/Scenario`。它包括版本化本体、战场几何、规则、共享态势产品、链路状态和审计索引。多个 Agent 可以读取它，但每个 Agent 是否可见由链路和授权决定。
2. **局部 Agent 上下文**的主体是 `AgentInstance`。它包括该 Agent 的 Observation、BeliefState、Goal、Task、Intention、Plan、局部资源和经验。它不是全局事实库；断链时只能使用仍然可见的内容。
3. **智能助手问答上下文**的主体是 `Operator + AssistantSession`。它保存问题、检索范围、回答、引用和请求草案。助手可以代替操作员查询 Agent 上下文，但回答不能直接成为作战事实、Agent 信念、Permit 或动作执行结果。

因此，本项目的作战 Agent 是 `AgentInstance`，而“智能助手问答”是外部会话代理。只有在明确授予其 `AgentInstance`、`AgentType`、`Authority` 和运行身份后，助手才可以代表一个作战 Agent 发起 `ActionRequest`；即使如此，`assertedBy`、`decidedBy` 和 `assistantActor` 也要分别记录。

## OpenViking 目录映射

| 本体对象 | OpenViking 目录 | 典型内容 | 写入者 |
|---|---|---|---|
| `Run` / `Scenario` | `viking://resources/coast-raid-k/runs/{runId}/` | 场景、规则、共享观测、运行摘要、审计索引 | 仿真器、传感器适配器、融合服务 |
| `AgentInstance` | `viking://users/{accountId}/agents/{agentIri}/runs/{runId}/` | 局部观测、信念修订、任务、计划、经验 | 对应 Agent 或受托运行服务 |
| `AssistantSession` | `viking://users/{accountId}/sessions/{sessionId}/` | 问题、检索轨迹、解释、请求草案 | 操作员、助手 |
| `AgentType/ShellTemplate` | `viking://resources/coast-raid-k/ontology/agent-types/` | 本体、模板、技能、接口说明 | 版本发布流程 |

目录范围先按 account、run、contextType 和 owner 过滤，再使用 OpenViking 的 L0/L1/L2 分层检索：摘要只用于筛选，授权和信念修订必须回读 L2 原始证据。OpenViking 的相似度分数只表示检索相关性，不能写成作战置信度。

## 一条可审计的闭环

```text
Observation
  -> BeliefAssertion（AgentLocalContext）
  -> Goal / Task / Plan
  -> Decision
  -> ActionRequest
  -> Gate Monitor Authorization
  -> ActionExecution / Effect
  -> AuditEvent（OperationalContext）
```

OpenViking 在这条链上承担三件事：保存带 stable IRI 的上下文记录，按 Agent/Run/会话范围检索，保留摘要和检索轨迹。它不承担三件事：不把文档相似度当置信度，不把助手回答当断言，不生成 Permit。

## 对当前代码的落地建议

- `platform/battle.js` 创建 `AgentInstance`、`Observation`、`Decision`、`ActionRequest` 和运行审计事件。
- `platform/knowledge.js` 维护 `BeliefState/BeliefAssertion`，保留 `derivedFrom`、`confidence`、`validTime` 和 `derivedBy`。
- `mayGate()` 只能作为 Gate Monitor 的策略评估入口，输出 `Authorization`；通过后才产生 `ActionExecution`。
- `platform/agent-runtime.js` 的 `RuntimeLedger.snapshot()` 是写入 OpenViking 的适配边界。生产环境应由适配器把快照拆成上述三类目录，并通过 OpenViking SDK/MCP 写入。
- `platform/openviking-adapter.js` 提供无传输依赖的 `snapshotToContextRecords()`、`assistantSessionRecord()` 和 `openVikingManifest()`；它只做边界映射，便于后续接入 OpenViking SDK/MCP，不在前端直接保存凭证或执行授权。
- 生产部署要启用 OpenViking 的 account/user 隔离和资源 ACL；ACL 只负责存储访问控制，不能替代本体中的 `Authority`、`Organization`、`Policy` 和 `Authorization`。

## OpenViking 项目研究依据

- [OpenViking GitHub](https://github.com/volcengine/OpenViking)：项目定位为 AI Agent 的上下文数据库，以 `viking://` 虚拟文件系统统一知识、记忆和技能。
- [Context types](https://docs.openviking.ai/en/concepts/02-context-types)：知识、记忆和技能分层组织。
- [Context layers](https://docs.openviking.ai/en/concepts/03-context-layers)：L0 摘要、L1 概览、L2 细节的渐进式读取。
- [Viking URI](https://docs.openviking.ai/en/concepts/04-viking-uri)：目录范围和资源定位。
- [Multi-tenant](https://docs.openviking.ai/en/concepts/11-multi-tenant) 与 [ACL](https://docs.openviking.ai/en/concepts/15-acl)：账号隔离和资源访问控制。

仓库当前为 AGPLv3。若把服务端代码或修改后的服务端作为网络服务提供，需要在选型和发布阶段单独评估许可证义务；本项目只依赖其上下文存储和检索接口。
