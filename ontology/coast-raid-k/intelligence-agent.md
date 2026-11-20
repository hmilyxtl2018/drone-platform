# 情报收集 Agent：证据采集与知识候选治理契约

本模块只处理公开资料或已获授权的内部文档。它把原始资料、证据声明、知识候选和已发布知识版本分层保存，任何模型输出都不能直接成为已发布事实，也不能调用 Effectors 或生成运行授权。

## 运行链

`SourceDocument → EvidenceClaim → KnowledgeCandidate → ConflictSet → IngestionReview → PublishedKnowledgeVersion`

- **SourceDocument** 保存不可变的原文、来源 URI、来源版本、访问依据、原文内容哈希和采集时间。
- **EvidenceClaim** 保存 Agent 或人工从原文抽取的结构化声明；每条声明都要指向原文定位。
- **KnowledgeCandidate** 是面向本体的入库候选，带修订号、概念匹配、不确定性和建议状态。
- **ConflictSet** 把同一匹配键上的互相冲突候选聚合起来，冲突解决前不能发布。
- **IngestionReview** 记录逐候选的人审动作：采纳、拒绝、要求补证或编辑。编辑必须生成新的候选修订。
- **PublishedKnowledgeVersion** 是经授权发布者检查后生成的不可变知识版本；它保存候选快照、候选 ID 列表和版本哈希。

## 证据要求

采集写入必须提供 `sourceUri`、`sourceVersion`、`contentHash` 和访问依据（`public` 或 `authorized`）。声明和候选必须提供原文摘录以及 `lineStart`、`lineEnd`；页码、段落和字符偏移可作为补充定位。内容哈希是原文 UTF-8 字节的 SHA-256，发布版本哈希是规范化 JSON 快照的 SHA-256。

文档一旦写入不可原地修改。来源更新应创建新的 `SourceDocument` 版本；候选编辑应递增 `revision`，并保留之前的审核记录。审核接口使用 `expectedRevision` 做并发保护，过期写入必须拒绝并留下审计事件。

## 人在回路和权限边界

提取 Agent（包括大模型）可以提出声明、概念匹配、冲突分组和审核建议，但只能产生结构化候选。人工审核产生 `IngestionReview`，不等同于发布。只有明确授权的发布者在证据完整、版本匹配、开放冲突已解决或明确驳回后，才能生成 `PublishedKnowledgeVersion`。

本模块不会创建动作请求、授权或执行器调用。若情报采集任务接入共享 RuntimeEnvelope，每个事件都带 `runId`、序号和 payload 哈希，事件流追加写入并可按序回放。

## 可选的大模型适配器

首版默认使用确定性提取器，因此没有配置 key 也能运行。配置服务端环境变量 `DEEPSEEK_API_KEY` 后，`DeepSeekClaimProvider` 会调用 DeepSeek 的 OpenAI-compatible Chat Completions 接口，要求模型返回 JSON 候选。模型可以提出：

- 来源行对应的 `subject | predicate | value` 关系；
- 对应 TBox 概念的 IRI 匹配；
- 关系的证据行、引用片段、不确定性和审核建议。

服务端只接受 TBox 目录中存在的 IRI，并重新从原文生成行号和引用片段。请求失败、超时、无 key 或 JSON 不合规时回退到确定性提取；回退不能提升发布权限。API 状态只公开 provider ID、模型名和是否已配置，不公开 key。大模型输出与规则输出都进入同一 `KnowledgeCandidate → IngestionReview → PublishedKnowledgeVersion` 门禁。

## Firecrawl 智能搜索

设置 `CRK_SEARCH_PROVIDER=firecrawl` 后，OSINT 任务的来源列表包含 Firecrawl 智能搜索。任务问题和关键词会组成搜索查询；返回的公开 HTTPS 页面标题、URL、摘要或 Markdown 内容按内容哈希保存为 `OSINTItem`，并记录查询、来源和采集时间。结果 URL 会拒绝凭据、非 HTTPS、localhost 和私有字面 IP；搜索结果仍是不可信材料，不能绕过证据审核。Firecrawl 只负责搜索/抓取，DeepSeek 负责本体概念和关系候选提取。

## API 形状

- `GET /api/intelligence/state`：返回 documents、claims、candidates、conflicts、reviews、versions 和 events。
- `POST /api/intelligence/documents`：提交标题、来源 URI、来源版本、正文、访问依据和来源可靠性。
- `POST /api/intelligence/candidates/{id}/review`：按 `expectedRevision` 写入逐候选审核。
- `POST /api/intelligence/publish`：发布已通过审核和冲突检查的候选快照。

## 作战无人机补库覆盖度门槛

全球作战无人机型号库必须显式标注覆盖缺口。项目采用“截至 2006 年已有 41 个国家、80+ 种作战无人机”的书面统计作为历史下限：低于该下限时，候选库只能作为 seed/under-complete 数据集展示，不能宣称覆盖充分。

运行 `python tools\audit_uav_ontology_coverage.py` 可以从 `coast-raid-k-intelligence.sqlite` 读取 `ontology_entity` 和 `ontology_assertion`，按服役年份、首飞年份、国家字段和实战状态筛选，输出型号缺口、国家缺口、未规范国家提法和缺失年份。该工具只读数据库，不发布事实；它用于决定下一轮 Firecrawl/人工补证任务的范围。


## 有限窗口的趋势评估

OSINT 采集的 RSS/Atom 模式只读取任务指定的最近窗口（sinceDays 为 1–365）和数量上限。它不是完整的 365 天历史档案：来源可能漏发、删改或只保留短期条目，所以没有返回某条材料不能解释为“没有发生”。每个 OSINTItem 仍保存来源、发布时间、正文摘要和内容哈希，供后续核验。

完成采集后，操作员可以从任务材料中选择条目生成 IntelligenceAssessment。报告有独立的修订号、来源快照、引用行范围、哈希和审核状态。summary.findings 中可记录：

- maturity：技术或项目成熟度的证据描述；
- procurementIndustry：采购、合同、供应链或产业信号；
- applicationImpact：对研究问题的应用影响描述；
- contraryEvidence：相反证据或尚未解释的材料。

这些字段属于研判报告，仍是待审核的分析内容，不会自动成为本体断言。

首版无模型时使用 signal-summary：只统计选定材料中的主题、采购和产业信号，明确列出证据缺口，不输出无依据的因果预测。配置 DeepSeek 后可生成 model-assessment 草稿；服务端会校验每个 finding 的材料 ID 和引用范围，只引用一条材料或含未知引用的 finding 会被丢弃；同一出版方的多条材料会降为低置信度。模型超时、无 key、返回格式错误或引用不合规时回退到确定性摘要，并在 warnings 中说明回退原因。两种模式都必须经过人工审核。

研判报告的“发布”只表示发布一份分析报告，和 PublishedKnowledgeVersion 的可信知识发布完全分离；它不会修改静态本体或知识库版本。人工接受、报告发布、知识候选审核和知识版本发布是四个独立动作。

已发布报告若被发现是错误的测试记录，可由授权操作员通过内部撤回操作记录 reason、actor、createdAt 和撤回事件。撤回只叠加审计状态 withdrawn，保留原始快照和原发布事件；该修订进入终态，不能重新审核或再次发布。

趋势评估接口为：POST /api/osint/tasks/{id}/assessments（可选 itemIds、horizonYears），GET /api/osint/tasks/{id}/assessments，GET /api/osint/assessments/{id}，POST /api/osint/assessments/{id}/review 和 POST /api/osint/assessments/{id}/publish。审核提交 action、reviewer、reason 和 expectedRevision；发布提交 reviewer 和当前 expectedRevision。内部撤回方法为 withdraw_assessment_publication(id, {actor, reason})，当前没有 HTTP 撤回端点。

本地单用户 Demo 的 reviewer/actor 仍由调用方提供，尚未接入生产身份认证。应用流程不应自动冒充人工点击审核或发布；测试通过临时数据库验证这些状态转移。

## 公开装备指标的量化分析

趋势报告可以增加可选的 `summary.capabilityAssessment`（`version: 1`），记录公开资料中的装备参数、测试结果、成熟度和保障指标。它说明“来源报告了什么数值、在什么条件下、证据是什么”，不生成杀伤概率、胜负推演、目标优选、战术优化、实时部署跟踪或综合战力分。旧报告缺少这个字段仍然有效；扩展沿用报告的 summary JSON 存储，无需数据库迁移，也不会写入可信本体。

可选的 `capabilityAssessment.provider`（字符串）和 `model`（字符串或 `null`）单独记录量化提取器。派生稿可以保留原报告的确定性趋势摘要，同时由 DeepSeek 提取新增量化指标；这两个字段不会覆盖原趋势报告的 provider/model，也不包含任何凭据。

每项指标包含 `id`、`equipment`、`dimension`、`metric`、`value`、`unit`、`qualifier`、`basis`、`conditions`、`assessment` 和 `evidence`。`dimension` 是可扩展的文字分类。`qualifier` 保留精确、约数、至少、至多、大于或小于的区别；`basis` 区分报道的测试结果（`reported-test`）、厂商声明（`manufacturer-claim`）、项目目标（`program-target`）和报道的观察值（`reported-observation`）。报道测试不等于独立验证，厂商声明和计划目标不能写成已验证的装备能力。测试条件不明或指标缺失须在 `gaps` 中说明，不能补成 0；条件或单位不同不能直接进行横向比较。

`evidence` 保存 `itemId`、`quote`、`startChar`、`endChar`、`sourceUri`、`sourceVersion`、`contentHash` 和 `publishedAt`（未知时为 `null`）。偏移针对完整的 `sourceSnapshot.body`，以 Unicode 码点计数，起点为 0，终点不包含在摘录中：必须满足 `0 <= startChar < endChar <= len(body)` 且 `body[startChar:endChar] == quote`。它不是 UTF-8 字节偏移，也不是 JavaScript 的 UTF-16 单元偏移。后端核对精确摘录、原始数值和来源元信息；`value` 保留源文数字，不自动换算单位，也不把百分数改成比例。单位、限定词、装备对象、指标含义和测试条件仍需人工核对，数值匹配本身不证明实战有效性。JSON Schema 仅验证字段结构，无法代替这些原文和跨字段检查。

`statistics` 由后端根据保留的指标重新计算：`metricCount` 是指标数，`equipmentCount` 是去重装备标签数，`citedSourceCount` 是去重材料 `itemId` 数，四种 `basis` 各自的计数之和等于指标数。这些数字表示证据覆盖，不是装备性能或独立出版方数量。

`POST /api/osint/assessments/{id}/capabilities` 接收空对象 `{}`，只从该报告已保存的 `sourceSnapshot` 提取，返回 `{ok, assessment, duplicate}`。系统保留原报告，创建新的 `under-review` 草稿，并在 `capabilityAssessment.basedOnAssessmentId` 记录原报告 ID。已发布报告可以派生新稿，已撤回报告不可派生；新稿不继承原报告的审核或发布决定，必须独立完成既有人审流程。后续完整报告生成使用相同的量化结构。

重复提取若派生内容哈希与已有派生版本完全相同，接口返回 `duplicate: true` 并复用该版本，保留它已有的审核或发布状态；这不新增审核，也不继承父报告的审核。首次创建的不同内容版本始终从 `under-review` 开始。
