# F04 身份迁移清单（M1）

状态：**SQLite 隔离候选投影与前端身份迁移已实施；PostgreSQL 实库验收未执行；当前服务尚未切换到候选版本。** SQLite 构建只写新目录，未覆盖现用数据库。完整语义候选的文件与数据库哈希以对应构建 manifest 和验收报告为准。

## 目标

- 以完整 IRI 作为跨文件、数据库、API 和前端对象的语义身份。
- 保留源文件已经给出的 IRI；Agent extension 的 IRI 不改写。
- 基础本体类缺少 `iri` 时使用 `base#<URI编码的类id>`，不创建 `Class#` 或 `Type#` 类命名空间。
- 基础个体缺少 `iri` 时使用 `base#individual/<type>/<id>`；类型和 ID 均按 URI 路径编码。
- 历史个体 `base#<id>` 仅作为兼容别名；先匹配完整 canonical IRI，再查别名。唯一别名可解析，碰撞别名必须返回 `ambiguous`。基础类的 canonical `base#<类id>` 不因此降格为别名。

## 当前影响项

| 类型 | 当前记录 | 当前身份问题 | 迁移结果 |
| --- | --- | --- | --- |
| `Rule/gate` | `rules.json` | 与 `FunctionBind/gate` 共享 `base#gate` | 使用类型限定的个体 IRI；旧 `base#gate` 返回二义性 |
| `FunctionBind/gate` | `algorithms.json` | 与 `Rule/gate` 共享 `base#gate` | 同上 |
| `AgentInstance` 三条 | `agent.instances.json` | inventory/SQLite 生成器曾使用 `base#<id>`，丢失源 `agent/...#...` | 保留源 `iri`，投影与 API 使用源 IRI |
| 48 基础类 | `tbox.json` | 无显式 `iri` | 直接使用 `base#<ClassId>` |
| 50 Agent 扩展类 | `agent.extension.json` | 有完整源 `iri` | 原样保留 |
| 其余基础个体 | `inventory.json` 对应业务 JSON | 无显式 `iri` | 使用类型限定的确定性个体 IRI，旧短 IRI 留作别名 |

## 实施步骤

1. 读取 `identity-registry.json`，建立 `(kind,type,id)`、canonical IRI、legacy alias 三套索引。
2. `load_db.py` 投影优先使用源 `iri`；无源 IRI 时调用 `identity.canonical_iri`，禁止无条件 `base + id`。
3. `crk_individual.iri` 增加唯一性验收；重复 canonical IRI 使构建失败。数据库主键仍保留 `(package, version, type, id)`，避免以 IRI 破坏兼容数据键。
4. `/api/kb/boot` 返回 `iri`，前端 entries 保留该字段；导航按钮、主视图链接、选择状态和地址均以完整 IRI 定位主体。`type:id` 仅保留作兼容标签。扩展类按 IRI 合并，同名类追加独立入口；旧层级树并未重写为通用的跨命名空间图编辑器。
5. 旧 `#Class/<id>`、`#<type>/<id>` 链接继续解析；唯一旧 `base#<id>` 别名跳转 canonical IRI，碰撞时显示二义性及可点击候选，不选择第一条。
6. SQLite 已通过隔离构建边界验证。PostgreSQL 的 DDL/投影代码已保留兼容实现，但尚未连接实际数据库运行迁移、唯一性和 API 内容对账；不得据此声称双数据库验收完成。发布指针切换仍是独立操作。

## 已执行的回归验证

- `audit/2026-09-29/test_build_boundary.py`：默认检查不写文件；构建只发布完整新目录；唯一性、编译、SHACL 引擎及源并发变更失败不覆盖现有发布。
- `audit/2026-09-29/test_navigation.mjs`：194 个主体在主题/模块导航均有完整 IRI 点击目标；同名扩展类不被吞掉；旧短 hash、唯一完整别名和二义别名候选均验证。
- `audit/2026-09-29/verify_semantic_candidate.py`：对指定隔离候选只读核对 manifest、SQLite `crk_part` 和实际 API boot 投影；不启动服务或初始化运行时数据库。

## 关闭证据

- 所有有源 IRI 的 canonical 值与源文件逐字一致。
- 生成物中 canonical IRI 无重复；`Rule/gate` 和 `FunctionBind/gate` 不再共享 canonical IRI。
- `base#gate` 的解析状态为 `ambiguous`，候选完整且可列出。
- 三个 AgentInstance 的 SQLite/API IRI 与 `agent.instances.json` 相同。
- 旧 hash 链接可解析；不明确的旧短 IRI 不静默选第一项。
- 构建失败不替换当前发布；迁移前后源 JSON 内容哈希不变。

## 回滚

保留旧 SQLite 和 API 发布目录，使用版本化发布指针恢复；不得通过把新 canonical IRI 改回 `base#id` 来“修复”兼容性。兼容别名继续保留到所有旧链接迁移完成。
