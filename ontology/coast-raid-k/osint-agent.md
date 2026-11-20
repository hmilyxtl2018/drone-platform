# 开源军事情报 Agent

首个 Demo 面向装备技术、采购与国防产业动态。操作员手动创建一个研究任务，填写研究问题、关键词、回看时间并从代码维护的公开来源白名单中选择 RSS/Atom 来源。任务由后台线程执行，页面展示来源读取、去重、关键词匹配和待核验材料生成四个步骤。

## 完整运行链

`OSINTTaskCreated → PublicFeedFetched → OSINTItemCreated → DocumentSubmitted → SourceDocumentStored → EvidenceClaimExtracted → KnowledgeCandidateCreated → IngestionReviewRequested → IngestionReviewRecorded → KnowledgeVersionPublished`

采集器只输出公开材料：标题、来源、发布日期、原文摘录、原文链接、来源版本、内容哈希和匹配词。材料先保存在独立的 OSINT 库中，选择后才交给 `IntelligenceAgent` 建立 `SourceDocument` 和候选声明。候选必须逐条经过人工采纳、拒绝、补证或编辑，存在未处置冲突时不能发布知识版本。

## 触发和模型边界

当前支持手动触发 Firecrawl 搜索、报告生成、实体事件抽取和复核。服务端配置 DeepSeek 后，报告生成实际调用趋势综合与公开指标提取；图谱抽取、反证复核由各自按钮单独调用模型。未配置时只生成材料统计和缺口，不伪造模型结果。模型只能提出摘要、关系和候选断言，发布必须有单独的人工审核记录。

## 公开来源与安全边界

来源由 `platform/osint_collector.py` 维护，当前包含 GAO Reports、Defense News Pentagon/Industry、CSIS Analysis 和 U.S. Congress Bills。采集只访问 HTTPS 白名单，拒绝私有/回环/保留地址、越权重定向、DOCTYPE/ENTITY、超大响应和任意文章链接跟踪。采集范围不包括实时兵力/人员跟踪、目标识别、作战任务、隐蔽或凭证化获取。

## 数据分层

`ddl-osint.sql` 对应独立的 `osint_source_allowlist`、`osint_task` 和 `osint_item` 表；`ddl-intelligence.sql` 保存证据治理对象；静态本体包和 Runtime Store 继续分离。OSINT 项目永远保持 `pending-review`，不可直接成为可信知识。SQLite Demo 使用 `platform/osint_store.py`，PostgreSQL 结构以 DDL 为准。

## 页面与接口

- 页面：`/osint.html`
- `GET /api/osint/state`：来源白名单和最近任务
- `POST /api/osint/tasks`：手动创建任务
- `GET /api/osint/tasks/{id}`：读取步骤、材料、摘要和错误
- `POST /api/osint/tasks/{id}/candidates`：把选中的材料送入 `/intelligence.html?api` 证据审核

页面中的“送入证据审核”是人在回路入口；它只提交材料，不代表事实确认或知识发布。

## 2026-09-30 报告、图谱与本体准入

报告显示范围、证据、图谱、趋势、能力、制造、验证和本体八部分；结构契约见 `schema.intelligence.json#/$defs/AssessmentReportSections`。已有报告继续读取原始 summary 与 sourceSnapshot，新图谱、验证轮次、准入候选分别保存，原报告不被改写。制造部分用于展示研究证据和缺口；没有证据时显示待补证，不生成制造参数或自动选型结论。

Firecrawl 的本地单次条目上限为 100（默认 20），query 至多 4000 字符，响应至多 4 MB，单条正文保留至多 20000 字符，默认超时 45 秒。这些是 Demo 处理边界，不是供应商套餐额度，也不保证能返回所请求条数。每次 DeepSeek 分析读取最多前 30 条、每条前 5000 字符，prompt 上限 180000 字符，输出上限 4096 token。图谱每次优先提取 10 个关键节点和 12 条关系，未覆盖内容仍需补证。

运行顺序为：搜索公开材料 → 选择材料 → 生成趋势与量化草稿 → 提取实体/事件关系 → 检查证据完整性 → DeepSeek 反证复核 → 按缺口另行搜索并生成新报告 → 人工审核 → 单独发布能力观测。每个阶段均可保留缺口；重复同模型/同材料复核不视为独立验证。同一报告最多 3 轮模型复核，达到上限后需补证生成新报告。轮数不能保证结论正确；模型不会自动提高置信度。

新增 SQLite 表由 `IntelligenceStore` 启动时迁移：`intelligence_graph_revision`、`intelligence_validation_round`、`intelligence_ontology_admission`、`intelligence_ontology_admission_review`。图谱和复核轮次不可修改、删除；准入断言必须逐字段匹配原能力账本，审核使用 expectedRevision 防止旧页面覆盖新审核。以下接口同时提供 `/api/intelligence` 与 `/api/osint` 别名：

- `GET/POST /assessments/{id}/graph`：读/构建图谱；POST 的 `extractEntities: true` 调用 DeepSeek，逐条验证输入摘录中的精确引文。
- `GET/POST /assessments/{id}/validation`：读取轮次/执行检查；POST 的 `kind: model-critique` 执行真实模型反证复核，默认 `deterministic` 仅检查证据结构。
- `GET/POST /assessments/{id}/admissions`：读/生成能力观测候选。
- `POST /admissions/{id}/review`：人工 accept、reject 或 request-more-evidence，必填审核人、理由和候选修订。
- `POST /admissions/{id}/publish`：只发布已人工通过的候选，必填发布人和候选修订。
- `GET /ontology`：查询有效已发布的能力观测、型号、关系及溯源。

`/api/kb/boot` 将已发布观测作为 `intelligence-ontology.json` 运行扩展展示；基础静态本体包不被覆写。词汇类和谓词使用 `https://local.ontology/coast-raid-k#`，实例使用 `/intelligence#`。父报告撤回后，对应图谱停止作为活动图返回、观测退出可信查询，历史记录仍保留。

验证采用临时 SQLite、随机本地 HTTP 端口和模型 mock，不会代用户审核真实数据。可运行 `python -m unittest discover -s demo -p "test_*.py" -q`。本轮真实 DeepSeek 抽取/复核仅生成研究记录和待审核候选，未代用户完成本体发布。
