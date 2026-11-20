# Agents 工作台 Demo

这个 Demo 验证的是知识治理运行链，Effectors 只写入范围受限、可逆的证据记录，不连接外部执行器。

## 启动页面

```powershell
cd D:\Ontology_Agents\platform
python -m pip install -r requirements.txt
python serve.py
```

打开 <http://127.0.0.1:8765/agents.html>。`kb.html` 继续用于本体、规则、来源和版本浏览；`agents.html` 默认连接 Runtime API，用于 Run、事件流、Copilot 建议、人工队列、Gate 状态、证据和回放；需要纯浏览器回放时使用 `agents.html?demo`。

本地 Demo 的最小 Python 依赖记录在 `requirements.txt`。PostgreSQL、DeepSeek、Firecrawl 与 OpenViking 都是可选外部依赖；未配置时页面应明确显示未连接或使用本地 SQLite/规则模式，不应伪装为在线服务。工作台 API 会写入 Runtime、Intelligence 或 OSINT SQLite；健康检查和 `kb.html` 的知识库读取不应触发外部采集或模型调用。

## 跑完整链路

```powershell
cd D:\Ontology_Agents\platform
node run-workbench-demo.js > run-workbench-demo-output.json
```

Runner 会验证：

- 没有 `HumanDecision(approved)` 时 Gate 不会发布 allow；
- 人工批准后才生成短时 Authorization；
- Effectors 只接受 active allow，并记录可逆证据采集结果；
- 人工拒绝、授权撤销、授权过期和失联都会得到 `aborted` 与安全回退审计；
- Trigger Dispatcher 只分发事件，不能授权或直接调用 Effectors；
- 输出 Trace 能按事件序列重放，payload 带 canonical JSON SHA-256。

## API

运行状态写入独立的 `coast-raid-k-runtime.sqlite`，静态知识库仍使用 `coast-raid-k.sqlite`。主要端点：

- `POST /api/runs`
- `POST /api/runs/{runId}/events`
- `GET /api/runs/{runId}/trace`
- `POST /api/runs/{runId}/human-reviews`
- `GET /api/runs/{runId}/human-queue`
- `POST /api/human-reviews/{reviewId}/decision`

RecommendationProvider、Copilot 和前端都只能产生结构化建议或人工决定；最终授权主体始终是 Gate。

## 装备本体候选导入与核验

### 无人机生产制造数据契约（v1）

`../ontology/coast-raid-k/manufacturing-extraction.schema.json` 定义 DeepSeek 后续提取使用的五类 JSON 输出：组成与构型（`composition`）、制造流程（`process`）、制造组织（`organization`）、生产与交付（`production`）、成本与价格（`cost`）。`manufacturing-model.json` 记录类别、关系、枚举和事实边界。每条 observation 必须绑定实体及可定位的逐字证据；schema 只验证数据形状，不代表事实已核验。

SQLite 迁移 `025`–`028` 提供生产/成本记录、五类待审候选、构型、组成项、制造流程及组织角色表。统一入口 `POST /api/ontology/manufacturing-extractions` 接受契约文档并创建 `under-review` 候选；随后仍须经 `POST /api/ontology/manufacturing-candidates/{candidateId}/review` 人工接受，再调用 `/publish`。`GET /api/ontology/entities/{entityId}/manufacturing` 返回五类正式记录和候选。

定向研究分为两个显式步骤：`POST /api/ontology/entities/{entityId}/manufacturing-research/collect` 使用 Firecrawl 按型号、别名、制造商和公开来源范围采集工业信息；`POST /api/ontology/entities/{entityId}/manufacturing-research/extract` 让 DeepSeek 按同一 schema 提取观察。`GET /api/ontology/entities/{entityId}/manufacturing-research` 返回当前检索任务、来源快照数量，以及 Firecrawl 和 DeepSeek 的配置状态。抽取只读取当前型号版本对应的已保存来源；每条引文都要精确匹配来源 URL、快照、型号主体和原文，验证失败时整批不写入候选。通过验证的候选仍为 `under-review`，必须人工审核后才能发布。采集失败或没有 API key 时，接口会返回明确状态，不会用模型回退结果冒充已完成抽取。

```json
{
  "schemaVersion": "uav-manufacturing-extraction.v1",
  "entity": {"entityId": "equipment-example", "name": "Example UAV", "variant": "Block 1"},
  "observations": [{
    "category": "cost",
    "configuration": "Block 1",
    "data": {"costType": "flyaway", "amount": 2500000, "currency": "USD", "priceBasisYear": 2025},
    "evidence": {"sourceUri": "https://example.gov/contract", "quote": "The flyaway cost was $2.5 million in 2025."}
  }],
  "gaps": []
}
```

装备工作簿先进入 `coast-raid-k-intelligence.sqlite` 的候选层，不直接修改静态 `coast-raid-k.sqlite`。导入按文件 SHA-256 去重，保存原始行、实体、别名、JSONB 属性版本、单元格证据和待审断言；所有记录初始为 `pending`，属性证据可定位到工作表、行号和单元格。

- `POST /api/ontology/import`，请求 `{ "workbookPath": "绝对路径.xlsx" }`
- `POST /api/ontology/import` 也接受 `{ "sourcePath": "绝对路径/agent-result.json" }`，将 Agent 输出的无人机平台、属性和关系导入候选层；同一文件按 SHA-256 去重。
- `GET /api/ontology/imports`
- `GET /api/ontology/entities?limit=100`
- `GET /api/ontology/entities/{entityId}`
- `GET /api/ontology/entities/{entityId}/attributes`
- `POST /api/ontology/entities/{entityId}/verification-plan`
- `GET /api/ontology/verifications?entityId={entityId}&status=planned`
- `POST /api/ontology/verifications/{verificationId}/execute`

核验计划按身份、性能、任务系统、协同、产业、实战和公开影像七类生成有边界的 Firecrawl 查询。搜索结果只形成 `unverified` 证据候选，模型提取仍须绑定逐字引文、人工审核和 OntologyAdmission 才能发布。

### 实体定向补证与批量运行

无人机实体可以走一条统一的对象级链路：读取当前档案和未知字段 → 根据型号、改型、别名、厂商、类别及 Excel 改型沿袭生成查询 → 按官方资料、评估/测试、采购、厂商、专业媒体和公开影像来源组循环采集 → DeepSeek 提出带单位、条件、改型归属、逐字引文和来源定位的属性候选 → 生成能力画像并执行 JSONB/语义校验 → 人工逐条审核和发布。档案已有值、来源版本和原始快照不会被覆盖；冲突保持并列；找不到的字段保持 `unknown`。

统一单实体入口是 `POST /api/ontology/entities/{entityId}/agent/enrich`（`/agent/research`、`/agent/enrichment`、`/agent/run` 为兼容别名），请求可传 `capabilityCodes`、`maxJobs`、`maxModelCalls`、`maxElapsedSeconds`、`maxResponseBytes`、`maxFollowupRounds` 和 `force`。返回的 `runId` 可通过 `GET /api/ontology/capability-runs/{runId}` 查看每轮采集、DeepSeek 提取、校验、缺口和停止原因。

批量入口是 `POST /api/ontology/capability-batches`（`/api/ontology/enrichment-batches` 为别名），请求传 `entityIds`，或传 `allEntities: true` 选择全部 `EquipmentModel`。每个实体独立创建能力运行和状态项；一个实体的失败不会阻塞其他实体。`GET /api/ontology/capability-batches/{batchId}` 返回每个实体的 `currentScope`、`round`、预算、`stopReason`、候选数、冲突数和错误；发布仍必须对能力记录逐条人工审核后单独执行。

若 DeepSeek 在来源引文中明确给出“目标实体—谓词—关联实体”，关系会进入 `ontology_relation_candidate` 候选队列，和属性候选一样保留快照证据、目标匹配状态和模型版本。关系审核使用 `POST /api/ontology/relation-candidates/{candidateId}/review`，接受后还要显式调用 `/publish` 才进入 `ontology_relation_assertion`；目标无法匹配本体实体时只保留待补证候选，不能直接发布。

### 实体审计任务边界

对象级补证也提供正式的 `AuditRun` 任务边界。对话只是创建任务的入口，状态、阶段事件、候选绑定、审核和发布结果都写入 `coast-raid-k-intelligence.sqlite`：

- 手动：`POST /api/ontology/entities/{entityId}/audit-runs`；
- 批量：`POST /api/ontology/audit-runs`，请求传 `entityIds`；
- 周期：`POST /api/ontology/audit-scheduler/run` 立即扫描，或创建持久化策略 `POST /api/ontology/audit-schedules`，由外部调度器调用 `POST /api/ontology/audit-scheduler/run-due`；
- 监控：`GET /api/ontology/audit-runs`、`GET /api/ontology/entities/{entityId}/audit-runs`、`GET /api/ontology/audit-runs/{auditRunId}`；
- 审核：`POST /api/ontology/audit-runs/{auditRunId}/review`，提交 `candidateType`、`candidateId`、`action`、`reviewer` 和 `reason`；
- 发布：`POST /api/ontology/audit-runs/{auditRunId}/publish`，只接受已通过审核的能力记录，并生成新的不可变 `ontology_entity_revision`；
- 失败或缺口任务：`POST /api/ontology/audit-runs/{auditRunId}` 重试，`.../{auditRunId}/cancel` 取消。

状态统一为 `queued → planning → collecting → extracting → reconciling → awaiting-review → completed`，证据不足时为 `completed-with-gaps`，并保留 `failed`、`cancelled`。每个审计结果绑定 `entityId`、`objectRevision`、`auditRunId`、来源快照和审核记录；旧版本不会被覆盖，回退通过重新发布旧内容生成新 revision。

### 多 Agent 分析运行

实体审计可以放入持久化的 `AnalysisRun` DAG，由七个角色协同推进：`ontology-planner`、`intelligence-collector`、`attribute-relation-extractor`、`signal-imagery-analyst`、`evidence-fusion`、`capability-quantification` 和 `red-team-governance-reviewer`。编排表记录每个 `AgentTask` 的依赖、尝试次数和状态；`AgentArtifact` 与 `AgentHandoff` 保存输入输出和证据链。所有 Agent 产物默认为 `under-review`，不会因为模型返回成功而进入正式本体。

- 创建：`POST /api/ontology/analysis-runs`，请求传 `entityId` 和预算；
- 监控：`GET /api/ontology/analysis-runs`、`GET /api/ontology/analysis-runs/{analysisRunId}`、`GET /api/ontology/analysis-runs/{analysisRunId}/tasks`；
- 执行：`POST /api/ontology/analysis-runs/{analysisRunId}/execute`，返回 `202` 后异步运行 Firecrawl/DeepSeek 审计适配器；
- 控制：`POST .../{analysisRunId}/pause`、`resume`、`cancel`、`retry`；
- 任务产物：`POST /api/ontology/agent-tasks/{agentTaskId}/artifact`，然后用 `/complete` 显式完成任务。

适配器会把现有 `AuditRun` 的采集、属性/关系提取、信号观测、证据融合和能力画像映射为上述七类产物，并将冲突、缺失证据和审核门控写入红队产物。分析运行完成只代表候选链完整，仍需调用既有审核和发布接口；适配器强制 `autoPublish=false`。

## 开源军事情报 Agent

本机已配置 Firecrawl 主搜索链。运行 `python serve.py --port 8775`，打开
<http://127.0.0.1:8775/osint.html>。后端从项目根目录 `.local/agent-secrets.dpapi`
读取仅当前 Windows 用户可解密的凭据，文件位于静态服务目录外；不会向浏览器返回 key。
也支持进程环境变量 `FIRECRAWL_API_KEY` 与 `CRK_SEARCH_PROVIDER=firecrawl` 覆盖。
Firecrawl 负责按研究问题搜索并抓取公开正文；失败会明确报错，不会自动转用 RSS。
材料保留来源 URL、查询、正文/摘要类型、截断标记和哈希；未知发布日期不会伪造。
随后可将材料送入 DeepSeek 提取和人工审核。以下 RSS 说明适用于显式 RSS 模式。

打开 <http://127.0.0.1:8765/osint.html>。首个主题是装备技术、采购与国防产业动态。操作员手动创建研究任务，选择代码维护的 GAO、Defense News、CSIS 或 Congress RSS/Atom 公开来源，查看采集进度和原文材料，再选择材料送入证据审核。

采集 Agent 只保存公开材料和待核验摘要，不直接写入可信知识库。来源访问限制为 HTTPS 白名单，拒绝私有地址、越权重定向、DOCTYPE/ENTITY、超大响应和文章链接跟踪。`osint_task`、`osint_item` 保存在独立 OSINT SQLite 库，PostgreSQL 结构见 `ontology/coast-raid-k/ddl-osint.sql`。

API：`GET /api/osint/state`、`POST /api/osint/tasks`、`GET /api/osint/tasks/{id}`、`POST /api/osint/tasks/{id}/candidates`。首版触发方式为手动；未来可增加定时或事件触发，但仍需经过人工证据审核和发布门。

运行 OSINT 测试：

```powershell
python -m unittest discover -s . -p "test_osint_collector.py" -v
python -m unittest discover -s . -p "test_osint_store.py" -v
```

## 证据治理 Agent

打开 <http://127.0.0.1:8765/intelligence.html?api>。如果端口已占用，可运行 `python serve.py --port 8775`，并使用对应端口。页面默认连接真实 API；`?demo` 是仅在浏览器内运行的交互示例。

首版通过人工粘贴公开或已授权正文触发，按每行 `主题 | 谓词 | 值` 提取候选；保留来源 URI、版本、原文行号、摘录和 SHA-256。候选经逐条采纳、拒绝、补证或编辑后进入发布检查；编辑创建新修订，必须再次采纳。有未解决冲突时禁止发布。

运行完整演示和测试：

```powershell
python run-intelligence-demo.py
python -m unittest discover -s . -p test_intelligence_agent.py -v
```

演示使用临时数据库：两份资料 → 四条候选 → 编辑/采纳/拒绝 → 冲突替换 → 发布 `v0001` → 审计事件。`--db 路径` 可保留演示记录。

数据写入独立的 `coast-raid-k-intelligence.sqlite`，可通过 `CRK_INTELLIGENCE_SQLITE_PATH` 指定路径。PostgreSQL 建表参考为 `ontology/coast-raid-k/ddl-intelligence.sql`；本轮服务使用 SQLite，未部署 PostgreSQL 迁移。版本发布保存候选快照，不自动修改静态知识包。

API 前缀为 `/api/intelligence`：`GET /state`、`/documents`、`/candidates`、`/conflicts`、`/reviews`、`/versions`、`/events`；`POST /documents`、`/candidates/{id}/review` 和 `/publish`。审核需提供 `reviewer`、`reason` 和 `expectedRevision`。

默认仍使用确定性提取器。需要启用 DeepSeek 的本体概念和关系候选提取时，在**后端启动服务的同一个 PowerShell 会话**中设置 key：

```powershell
$env:DEEPSEEK_API_KEY = "你的 key"
$env:DEEPSEEK_MODEL = "deepseek-v4-pro"        # 需账号模型列表支持；服务核对实际返回模型
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com" # 可选
python serve.py --port 8775
```

也可以使用不回显输入、且不把 key 写入 shell 历史或项目文件的启动器：

```powershell
python start_deepseek.py --port 8775
```

key 只由 `platform/intelligence_agent.py` 在服务端读取，不会返回到 `/api/intelligence/state`，也不会写入资料、候选、日志或前端。DeepSeek 只生成带原文行号和引用片段的候选声明：`subject | predicate | value` 表达关系，`concepts` 只能引用已声明 TBox 的 IRI；未知 IRI 会被过滤。候选仍必须经过证据核对、人工审核和冲突处置后才能发布。未设置 key、模型超时或返回格式错误时，服务自动回退到确定性提取，并在候选不确定性中标记 `llm-fallback`。

当前为本地单用户 Demo，操作员标识由界面提供，生产环境的认证和发布者权限校验尚未接入。本体匹配是对已声明 TBox 概念的建议，冲突检查覆盖本情报库中的同主题/谓词，不代表对整个静态知识库进行语义推理。相同内容按哈希去重并沿用首次来源记录，不应当作多个独立来源的相互佐证。


## 趋势研判与报告发布边界

完成 OSINT 采集后，可以在任务中选择材料并生成趋势研判。RSS/Atom 只按任务的 sinceDays（1–365 天）读取最近窗口，且受条数和响应大小限制；它不是完整的 365 天历史档案。来源可能漏发、删改或只保留近期条目，采集结果只能说明“本次窗口中看到的公开材料”。

研判默认使用 deterministic signal-summary，统计主题、采购和产业信号并列出证据缺口。配置 DeepSeek 后使用 model-assessment 草稿；模型只能根据服务端保存的材料快照提出 findings，引用必须通过材料 ID、行范围和跨来源校验。无 key、超时、格式错误或引用不合规时回退到确定性摘要，并在报告 warnings 中记录。maturity、procurementIndustry、applicationImpact 和 contraryEvidence 是报告字段，仍然属于分析内容。

审核链分为“人工接受研判 → 发布分析报告”和“审核知识候选 → 发布 PublishedKnowledgeVersion”。报告发布不会修改静态本体、知识候选或可信知识版本。错误的测试报告可以由内部撤回操作记录 actor、reason、createdAt 和审计事件；撤回后的修订为终态，不能重新审核或再次发布，原始快照仍可回放。

趋势研判 API：

- POST /api/osint/tasks/{id}/assessments：可传 itemIds、horizonYears；
- GET /api/osint/tasks/{id}/assessments、GET /api/osint/assessments/{id}；
- POST /api/osint/assessments/{id}/review：传 action、reviewer、reason、expectedRevision；
- POST /api/osint/assessments/{id}/publish：传 reviewer、expectedRevision。

当前撤回仅提供内部 withdraw_assessment_publication(id, {actor, reason})，没有 HTTP 撤回端点。Demo 是本地单用户流程，reviewer/actor 由调用方提供，生产身份认证尚未接入。

### 公开装备指标的量化分析

报告的可选 `summary.capabilityAssessment` 保存公开资料中可核对的装备参数、测试、成熟度和保障指标。每项显示源文数字和单位、约数或上下限、数据依据、测试条件、分析说明及精确原文证据。报道的测试结果、厂商声明、项目目标和报道的观察值分开展示；数据不足会列为证据缺口，不能用 0 代替，也不能把计划目标视为已验证能力。这里不生成综合战力分、杀伤概率、胜负推演或战术优化。

已有报告可调用 `POST /api/osint/assessments/{id}/capabilities`，提交空对象 `{}`，从其持久化材料快照生成新的待审核草稿。接口返回 `{ok, assessment, duplicate}`，新稿通过 `summary.capabilityAssessment.basedOnAssessmentId` 关联原报告。原稿保留；已发布报告可派生新稿，已撤回报告不可派生。新稿需要重新人工审核后才能发布分析报告，不会写入可信本体。该扩展使用已有 summary JSON，无需数据库迁移，旧报告也无需补写。

重复提取的派生内容哈希若与已有派生版本完全一致，接口返回 `duplicate: true`、复用已有版本并保留其已有审核或发布状态，不新增或继承审核。首次创建的不同内容版本始终为 `under-review`。

数值证据使用完整原文的 Unicode 码点偏移 `[startChar, endChar)`，后端核对精确摘录、数值及来源版本/哈希，不自动换算单位。单位、上下限、装备和指标含义、测试条件仍由审核人核对。指标统计只代表提取覆盖；其中 `citedSourceCount` 是不同材料 ID 的数量，并不等于独立出版方数。结构和详细约束见 `ontology/coast-raid-k/schema.intelligence.json` 与 `intelligence-contract.json`。

### 五个研究视图

`osint.html` 按采编执行、对象分析、图谱洞察、趋势研判、洞察结论排列。任务标题保持一行，点击标题后的 mini help 图标查看完整任务提示词，按 Escape 或点击外部关闭。军事来源及类型在左侧采编设置中选择。

采编执行显示保存的材料及真实工具调用；`GET /api/osint/tasks/{id}/execution` 返回观察到的调用状态、模型、耗时和计数，历史未记录调用不补造。执行记录以附加表保存，不改写已有资料。

对象、关系、事件与能力观测的核验结果绑定报告及图谱版本，显示在对应对象下。连续引文、位置、版本与哈希检查属于出处检查，事实含义及来源独立性仍待人工核验。模型反证复核不自动提升置信度。图表可打开所用来源，材料覆盖计数不代表装备能力增长。

示例预设由 `research-presets.json` 提供，完整任务提示词见 `../docs/task-prompts/tactical-uav-cooperation.md`。模型只读取有字符预算的原文窗口，超出窗口的材料不得声称已阅读；所有研究对象和结论保持待审核，发布通过独立的人工流程进行。

### 对象定向采编与影像作证

首组检索表达式中的具体对象名称作为过滤锚点，其他关键词用于限定属性或公开试验背景。采编记录显示实际查询及对象不匹配、泛化目录等过滤计数。模型优先提取来源明确给出的装备型号，再提取关联技术、组织、项目及事件；厂商名称不能替代型号，无法引用的属性保持缺口。

左侧可选“公开遥感影像档案”，调用 Firecrawl v2 图片搜索。采编执行中的“影像来源与对象证据”显示图片线索，并可登记原始出处、采集日期、许可、可见内容、解读及支持局限。未确认类型的图片不会自动标为卫星影像，图片检索标题不会输入模型作为装备事实。登记记录可绑定当前图谱的对象、关系及具体属性，并显示在对应对象下；图谱更新后需重新核验关联。影像引用不自动提高置信度或写入本体。接口与字段见 `../docs/public-imagery-evidence-contract.md`。
