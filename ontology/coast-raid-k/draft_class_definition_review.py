"""Build a review register from individually authored conceptual discriminators.

This source-maintenance command writes only class-definition-review.json. It
does not add ABox individuals, approve meanings, compile OWL axioms, or infer
conformance from illustrative examples. Re-run deliberately after source edits.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
B = "https://local.ontology/coast-raid-k#"
A = "https://local.ontology/coast-raid-k/agent/1.0.0#"

# Each row is individually authored: id | distinguishing features | included
# boundary | excluded boundary | positive conceptual example | negative
# conceptual example | alternative class IDs | outstanding review question.
# Semicolons within the feature cell separate discriminators, not OWL axioms.
REVIEW_ROWS = r"""
Entity | 本包可给予身份并作陈述的最一般对象；覆盖持存体、发生体以及文档层类对象 | 只断言对象在本包中可被谈论，不额外承诺物理性或可执行性 | 页面装饰性分组标题没有对应语义对象时，不因可点击而成为本体实体 | 把假设的一台观测设备作为被描述对象 | Navigation 中仅用于折叠布局的分隔标题 |  | 类作为实体的文档层用法与 OWL punning 的导出范围是否逐项登记？
Continuant | 在存在期间保持可追踪身份；与具有时间部分的发生过程区分 | 物体、空间区域以及依赖承载者的质量和信息对象等持存对象 | 过程的某个持续时段不是因此成为持存体 | 同一假设设备在不同观测时刻保持设备身份 | 该设备一次启动过程 | Process | 需要给出身份保持和替换判据，尤其是软件或组织的版本变化。
Occurrent | 本体类别以发生或时间延展为核心；包括本包声明的过程、运行及时间区间 | 发生事项及本包纳入此上位类的时间范围 | 关于发生事项的日志载荷是信息对象，不因含时间戳而成为发生体 | 假设的一次有起止边界的模型运行 | 记载该运行的 JSON 日志文件 | InformationEntity | Run、AuditEvent、AssistantSession 与 TimeInterval 的并列边界和外部上位本体对齐需领域复核。
IndependentContinuant | 具有自身可区分身份；不作为另一对象所承载的质量或角色而存在 | 本包中的物理对象、组织、Agent 和空间对象按各子类限定 | 设备可用性、承担角色和模型文档不按此定义归类 | 假设的一台具有独立资产身份的设备 | 该设备某时刻的可用状态 | Quality | 本包把 Agent 与 Organization 放入该类的依据需要与其软件、集体实现方式联合审议。
DependentContinuant | 其存在或实现依赖承载者；不与承载者拥有同一身份 | 本包中的质量、角色、能力、关系体和信息对象 | 独立设备或已发生过程本身不属于这种依存项 | 假设设备所承载的就绪状态 | 承载该就绪状态的设备本体 | MaterialObject | 专属依赖与可跨载体复制的信息内容尚未细分，不能直接宣称等价于单一外部类别。
MaterialObject | 物理存在并占据空间；在不同时间保持可识别身份 | 场景中具有物理承载的设备或平台 | 几何覆盖范围、类型目录和设备软件记录不是该物体本身 | 假设的一台传感设备实体 | 描述该设备类别的平台型谱条目 | PlatformType | 资产替换、拆分和组合时的物体身份策略尚需明确。
SpatialRegion | 表示空间范围及其几何边界；区域身份不以物质占据为必要条件 | 本包的几何域、包络、走廊和扇区等范围 | 位于范围中的设备及其能力不等于区域 | 在工程坐标中定义的假设多边形区域 | 位于该多边形内的一台设备 | MaterialObject | 确切区域、近似足迹和外包框必须有不同表示角色，几何等价判据待定。
InformationEntity | 以可表达、复制或引用的内容为身份依据；依赖载体但区别于载体 | 规范、模型、描述、判断和记录等信息内容 | 所描述的物体、实际过程或承载媒介不因被描述而变成信息内容 | 一份假设的设备类别说明所表达的内容 | 说明所描述的设备物体 | MaterialObject | 文档载体与内容实体的身份、版本和派生关系尚未全部形式化。
Quality | 依存于具体承载者；表示承载者在有效时间中的状态或品质 | 设备或 Agent 的状态，以及本包声明的时间尺度品质 | 描述质量的断言、测量值字符串与变化过程不是质量本身 | 假设链路在某时刻所具有的可用性 | 关于该可用性的一条带来源断言 | BeliefAssertion | 质量本体、质量值与质量断言三者的字段归属需要逐 schema 审议。
Role | 依赖承担者及语境；表示承担的职责而非主体身份 | 本包监视、职能以及 Agent 角色 | 角色规范文件、角色承担者和履职过程均需分别指称 | 某假设主体在审查任务中承担的监视角色 | 承担监视角色的软件主体 | SoftwareAgent | 现有角色目录记录是否表示可复用角色类型而非具体依存角色，需要迁移评议。
Relator | 将多个参与对象及关系语境实体化；与关系两端对象分别有身份 | 成员、通信需求、角色分配和平台绑定等关系体 | 参与对象本身以及仅用于界面连线的几何线段不是该关系 | 假设 Agent 与平台之间有有效期的绑定关系 | 绑定关系中的平台本体 | Platform | 参与端、有效期及关系同一性的最小约束尚未全面统一。
Process | 实际在时间中发生；具有参与者及可区分的时间部分 | 行为、通信、任务执行和状态变化等发生过程 | 模板、步骤列表与过程记录仅描述过程，不证明发生 | 假设的一次实际消息接收过程 | 关于消息接收步骤的流程规范 | ProcessSpecification | 历史 Stage 和 Task 数据含规范性描述，应逐实例检查是否误置到过程类。
Class | 在文档层表示一个类对象；区分所表示的类与该类的成员 | 本包用于浏览和描述本体类的类对象 | 某个 Sensor 个体不是 Sensor 类本身 | 文档层描述 Sensor 类的条目 | 一个假设 Sensor 个体 | Sensor | 文档层 Class 实例化与 OWL 类表达式需显式映射，不能因短名同一而混并。
Package | 聚合词汇、记录、约束与版本约定；具有包级范围与来源 | 一个可引用版本的知识内容集合 | 包级单位和文件清单不自动成为所有类或个体的固有属性 | 一份假设知识包的指定版本内容 | 包内某个传感设备个体 | Sensor | 包版本和运行模型版本是否独立演进、如何固定依赖需明示。
Exclude | 声明模型范围排除或禁止纳入的事项；属于范围信息 | 某知识包明确不建模的概念或功能声明 | 缺少数据本身不能证明明确排除；排除项不是场景对象 | 声明某外部协议不在本包建模范围内的条目 | 某协议数据暂未采集这一事实 | InformationEntity | 未建模、明确禁止与暂缺资料应细分理由，避免把排除当不存在断言。
Shoreline | 在指定工程框架内表示海陆分界曲线；区分数学曲线与采样近似 | 场景边界曲线及其明确版本 | 海域面、沿岸物体以及曲线的粗略外包框不是同一岸线对象 | 假设沙盘的参数化海陆分界曲线 | 由岸线划出的整个海域区域 | Region | 当前上位类较宽，曲线是物理边界、空间区域还是表示内容的细分需审议。
Region | 以边界或约定划定空间域；本包海陆划分是区域判别约定 | 按工程坐标与分类规则定义的空间范围 | 区域成员关系不蕴含占领、管辖或控制关系 | 假设场景的海域空间范围 | 某对象属于海域的成员关系 | Membership | 海陆代码枚举是否只用于本包 Region 子域，须避免推广成所有空间区域枚举。
Membership | 同时指明成员对象与所属区域；归属判据属于关系语境 | 某对象按明确规则属于某区域的关系 | 成员对象、区域本身和几何相交判断算法分别建模 | 假设设备按指定归属规则属于某区域的关系 | 该归属规则的计算方法说明 | Algorithm | 容纳、相交、代表点落入等不同归属规则尚需明确相互可替代性。
Envelope | 以参数限定三维空间可行或覆盖范围；区域与产生范围的设备区分 | 在坐标、单位和版本明确时定义的空间包络 | 设备能力、授权范围和实际效果不是单凭几何包络可得 | 假设模型中一个参数化空间包络 | 形成该范围的设备所具备的能力 | Capability | 原始体、投影足迹和外包框的精度声明与用途边界须逐字段审议。
Corridor | 表示具有形状和位置的连续空间通道；区分通道与使用它的过程 | 供通行或分析使用的空间范围 | 行进方案、路径点序列和实际运动轨迹不是自动等价的走廊 | 假设沙盘中划定的一段三维通行区域 | 穿过该区域的一次运动过程 | Process | 走廊中心线、横截面和边界的表示关系以及近似误差需登记。
SearchSector | 以中心、方向及范围限定扇区空间；区分扇区本体与其外包矩形 | 参数化搜索扇区表示的空间范围 | 搜索行为、观测结果和外包框不是扇区本身 | 假设模型的一块扇形空间区域 | 在该区域内发生的一次观测行为 | Process | 角度方向、开闭边界及高度范围需以统一坐标约定说明。
DetectionDisk | 以中心和半径定义圆盘范围；表示几何而非探测成功断言 | 明确坐标平面中的圆盘区域 | 观测记录和传感器探测能力不能由入盘关系直接推得 | 假设工程平面中定义的圆盘区域 | 报告某对象被观测到的信息记录 | Observation | 二维圆盘与三维覆盖之间的外推限制需要写入类使用说明。
JamBloom | 表示影响范围的参数化空间描述；不把范围存在等同于效果发生 | 本包用几何表示的假设影响区域 | 设备、实际电磁过程或效果记录均是其他语义对象 | 假设模型给出的干扰影响空间范围 | 对一次状态变化所作的效果报告 | Effect | 参数对应的有效条件和不确定性未充分定义，不能把外包范围当效果保证。
CommandPost | 指称场景中的物质设施；设施身份区别于使用它的组织 | 具有场景物体身份的指挥设施 | 软件主体、组织层级和授权职位不因名称相似而成为设施 | 假设场景中承担指挥支持用途的一处实体设施 | 使用该设施的组织 | Organization | 设施、组织与位置记录在历史 Site 数据中的界线需逐记录审议。
Sensor | 指称取得观测的物理设备；设备、观测和算法分别有身份 | 有物理承载且被登记为传感器的设备 | 观测消息、感知算法和设备类型条目不属于该设备个体 | 假设的一台测量设备实体 | 该设备输出的一条观测内容 | Observation | 可观测量、接口和校准状态是否属设备、能力或记录，需要明确所有权。
Shooter | 按现有父类指称效果实施的物质设备；拥有设备身份不构成执行权 | 本包现有类别所指的物理效果设备 | 授权决定、执行主体的软件角色和已发生动作均不等于设备 | 假设沙盘中登记的一台效果设备实体 | 允许某个请求继续处理的授权结果 | Authorization | 中文显示名与 Shooter 既有 IRI 的范围是否一致，以及多用途设备如何分类，待领域评议。
Platform | 表示物理承载单元；与平台类型和被实现的软件主体区分 | 有独立物理身份的设备承载平台 | PlatformType、AgentInstance 以及平台绑定关系都不是平台本体 | 假设的一台承载传感设备的平台实体 | 关于此类平台的一条型谱规范 | PlatformType | 组合平台、平台组件和软件托管的身份粒度需统一。
Emitter | 指称产生信号或辐射的物质设备；区分设备与某次发射过程 | 场景中登记的物理发射设备 | 信号内容、覆盖几何和具体发射过程不是设备本身 | 假设的一台信号发射设备实体 | 该设备某次产生信号的过程 | Process | 发射源位置估计与已知设备身份不能未经证据直接合并。
PlatformType | 类型目录内容属于信息实体；使用标记不改变其本体类别 | 描述一类平台的参数、分类与参考特征 | inScene=true 不把型谱个体变为 Platform，也不证明实际平台已创建 | 假设的一种平台类别规范 | 按该类别制造的一台具体平台 | Platform | 旧 inScene 文案已纠正；实际使用型谱与实例化记录的连接仍需逐条核验。
Needline | 表示参与方之间预期的信息需求；区别于现成可用通信链路 | 谁需要从谁取得何类信息的关系 | 需求关系的存在不表示通信服务已建立或消息已传递 | 假设两个主体之间的定期状态报告需求关系 | 同一对主体当前存在的通信链路关系 | Link | 需求方向、消息类别和有效范围需与 Link 和 Protocol 逐字段区分。
Link | 表示通信参与对象之间的链路关系；链路拓扑与实时状态区分 | 指定端点、方式或拓扑语境的通信关系 | LinkState、Message 和 CommunicationAct 是不同对象 | 假设两个节点之间的一条通信连接关系 | 该连接在某时刻的延迟状态 | LinkState | 物理媒介、逻辑链路和可用性记录是否共用短名需审议。
Invariant | 在适用范围内要求持续满足；属于条件规范而非运行事实 | 对状态或交换对象施加持续限制的规则内容 | 一次校验通过报告不等于不变条件本身，更不证明未来持续成立 | 假设模型规定标识符在一次运行内唯一的约束 | 某次唯一性检查的结果记录 | InformationEntity | 条件表达语言、量化范围和检查频率尚需统一，不能把文字约束当已执行证明。
Rule | 表达条件、模态或判定要求；规则内容与执行器分离 | 有明确适用语境的规范规则 | 规则代码的某次运行、结果和名为 gate 的角色是不同对象 | 假设的一条记录有效期检查规则 | 承担检查责任的职能角色 | FunctionBind | 规则的声明、实现与可执行覆盖之间应保持可追溯映射。
Algorithm | 描述可重复的计算方法；方法规范与某次执行区分 | 输入输出和方法步骤明确的算法说明 | 绑定它的角色、代码运行过程和运行结果不因此等同于算法 | 假设的一份排序计算方法规范 | 按该方法完成的一次排序过程 | Process | 名称、接口契约和实际实现版本是否一致需要逐算法验证。
CloudDef | 以期望、熵、超熵等特征描述云模型；模型定义本身是信息内容 | 本项目采用的不确定性模型参数定义 | 它所描述的质量、一次随机采样值或外部世界状态都不是模型定义 | 假设的一组带语义说明的云模型数字特征 | 由模型产生的一次采样数值记录 | InformationEntity | 参数单位、意义与引用论文的精确对应尚未逐项验证。
ShellTemplate | 可复用的运行结构描述；规定阶段及读写或绑定契约 | 壳体架构、阶段顺序和平台类型绑定的模板信息 | 某次 Run 的 AgentInstance 以及实际 AgentExecution 不是模板 | 假设的一份观测处理主体壳体模板 | 依据该模板在某次运行中建立的 Agent | AgentInstance | 与 AgentType 的关系目前不能直接声明 owl:equivalentClass，须给出显式映射。
Monitor | 表示承担监视责任的依存角色；区别于监视程序与监视结果 | 某承载者在规则检查语境中的监视职责 | 软件主体、监测算法、约束条目和审核事件不是该角色 | 假设质量审查主体所承担的监视角色 | 执行审查的软件主体 | SoftwareAgent | 现有目录项若仅为角色规范，应与具体角色实例及承担者区分登记。
FunctionBind | 以现有 Role 父类限定为职能角色；绑定说明是对角色履行条件的描述 | 某主体在明确语境中承担的有输入输出契约的职能 | 描述角色的配置记录不是角色本身；同名 Rule/gate 也不是同一对象 | 假设主体在数据检查流程中承担的 gate 职能角色 | 描述该角色输入输出的配置内容 | InformationEntity | 历史 FunctionBind 个体记录以配置描述为主，是否需分离角色规范与具体角色尚未裁决。
Stage | 以现有 Process 父类限定为实际过程片段；相对所属运行具有时间位置 | 一次实际过程内可界定参与者和时段的片段 | 壳体中的阶段定义、顺序号和输入输出模板不是已发生阶段 | 假设的一次数据读取过程中的实际校验阶段 | 模板中名为“校验”的阶段定义 | ProcessSpecification | 历史 Stage 目录以模板阶段为主；文本冲突已修，但实例重分类/规范拆分仍必须逐条审议。
Track | 表达观测或估计状态的关联序列；记录与被跟踪对象分离 | 对假设对象运动或状态进行持续关联的信息内容 | 真正物体、物体实际运动与完备真值不是航迹记录 | 假设的一条带观测时间和来源的状态关联记录 | 被此记录描述的物理对象 | MaterialObject | 航迹合并、拆分、重识别和不确定性如何影响记录身份需定义。
Identity | 表示带依据的身份或类别判断；判断内容不等于真实身份 | 对对象类别、归属或关联所作的认定记录 | 该对象自身、纯字符串标识符和未作判断的原始观测不等价 | 假设的一条注明证据的类别判断记录 | 被判断对象的实际物体身份 | MaterialObject | 判断状态、证据充足性和真值之间不应使用同一个确认标记。
Pair | 表达参与对象之间的候选或选定配对；记录与配对参与者分离 | 有明确参与端和选择状态的配对内容 | 实际协同关系、参与主体或配对算法不是配对记录 | 假设任务与可用资源之间的一条候选关联记录 | 计算候选关联的算法规范 | Algorithm | 候选、选中与执行中的关联是否应分状态并标明有效期需审议。
Slot | 表达离散资源或时间安排单元；安排存在不证明占用发生 | 资源预算或时间排程中的槽位记录 | 物理资源、时间区间本身和实际执行过程分别建模 | 假设校验服务日程中的一个预约槽记录 | 该槽位覆盖的时间区间 | TimeInterval | 槽位容量、占用状态与实际消耗的来源及更新语义尚需明确。
Permit | 旧模型专用许可产品；记录表达一次包内检查结果 | 现有模型生成的许可信息记录 | 不自动等价于 Agent Authorization，也不等于实际执行过程 | 假设旧校验链产生的一份许可记录 | 某主体随后实际执行的过程 | Process | Permit 到 Authorization 的字段及语义对应必须单独批准，不能以名称相近替代。
Wave | 组织一组有限任务或产品的信息单元；记录与实际过程区分 | 本包对某批任务或产品所作的分组描述 | 批次的真实开始、执行和完成不是该记录本身 | 假设一批数据审查任务的组织记录 | 此批任务的一次实际运行过程 | Run | 批次成员、版本与执行状态应分层表述，避免与 Run 身份混用。
Channel | 表示交换或资源通道的信息内容；容量描述区别于通信行为 | 某通道及其容量、占用等的记录 | Link 关系、Message 内容和 CommunicationAct 过程各有独立类别 | 假设的一条通信通道容量登记 | 利用该通道发送某条消息的过程 | CommunicationAct | Channel 记录与实际链路端点、媒介及动态状态的对应需明确。
Inject | 描述受控实验输入或扰动；注入规范不证明世界事件已发生 | 仿真测试中计划注入的变化内容 | 外部实际事件和实际注入过程不是该规范记录 | 假设一条测试中模拟缺失数据的注入说明 | 真正发生的一次输入替换过程 | Process | 计划、已提交、已生效注入应有不同状态及证据。
Event | 以现有 InformationEntity 父类指称事件记录；时间和载荷描述发生事项 | 某事项的记录信息，包括可重放的描述内容 | 被记录的发生事项本身是 Occurrent，不由记录存在直接证明 | 假设一条记录服务启动事项的日志内容 | 那次服务启动过程 | Process | 与 AuditEvent 的内容/发生二分及回放载荷身份需要统一。
Agent | 在环境中接收局部观测并维护内部状态；能在权限边界内选择和请求动作并承担追踪身份 | 符合本包决策主体边界的主体，具体运行归 AgentInstance | 物理平台、角色、单个语言模型文件和问答会话不自动构成 Agent | 假设具有独立状态和请求接口的数据检查主体 | 该主体使用的一份模型配置文件 | InformationEntity | 自治、目标维护和责任身份的必要判据是否都须满足，以及被动组件界线待审议。
AgentInstance | 具体 Agent 主体归属某一次 Run；生命周期、模型版本和权限边界可追踪 | 在特定运行中具有独立主体身份的 Agent | 可复用 AgentType、壳体模板和一次执行过程不等于该主体 | 假设运行中建立的某个数据检查 Agent 主体 | 该 Agent 使用的可复用类型规范 | AgentType | 跨运行复用、重启和迁移后的身份同一性规则仍需明示。
AgentFunction | 定义可组合的责任域；功能域自身不授予权限也非互斥主体子类 | World、Self 等职责分类的信息定义 | 相应 Agent、具体角色承担和授权结果各是不同对象 | 关于 World 只维护观测与推断边界的一份责任域说明 | 在某次运行中承担 World 职责的 Agent 主体 | AgentInstance | 七类功能如何组合及何种冲突组合被禁止仍需规则化，不能仅靠名称约定。
OperationalTimeScale | 按现有 Quality 父类表示主体责任的时间尺度；尺度与时钟值、单次持续时间区分 | 某 Agent 责任语境下的即时或其他时间尺度品质 | 秒数、tick 值和时间区间记录不自动等于责任尺度品质 | 假设主体在即时反馈语境中具有的时间尺度定位 | 某次过程的十秒持续时间记录 | InformationEntity | 枚举值是尺度类别还是质量个体，及同主体多尺度承载关系需要审议。
OrganizationModel | 组织结构的可引用描述；区别于被描述的真实或仿真组织 | Agent 类所属组织、编组和关系的模型内容 | Organization 实体、RoleAssignment 关系和通信链路不是此模型 | 假设团队成员与职责结构的一份模型说明 | 模型所描述的团队主体 | Organization | 模型版本、组织快照和有效运行范围尚需分开登记。
AgentClass | OrgMAS 四元组的结构性规范；角色、目标和组织模型组成描述内容 | 按本包采用的 OrgMAS 表述给出的 Agent 类规范 | OWL Class 本身、AgentType 架构规范与 AgentInstance 均不自动等价 | 假设由名称、角色集、目标集和组织模型构成的一份规范 | 依照该规范建立的运行主体 | AgentInstance | AgentClass 作为信息对象与 OWL 类以及 AgentType 的对应边界须正式裁决。
AgentType | 规定主体架构、能力和失败模式；类型内容区别于运行实例 | 可复用 Agent 类型规范及其状态、阶段约定 | ShellTemplate 只有显式映射才可作其表示；运行主体不是类型 | 假设一种数据审查 Agent 的架构与能力规范 | 按该类型创建的某个运行主体 | AgentInstance | 旧壳体与新类型的关系、必需约束及缺失类型个体需继续审议和补证。
AgentRole | Agent 在任务组织语境中承担的依存职责；角色不承载完整主体身份 | 有明确承担者和语境的传感、监视等角色 | Agent 主体、功能域定义和一次授权决定不是角色 | 假设 Agent 在一次质量检查中承担的审查角色 | 承担审查角色的 Agent 自身 | Agent | 可复用角色类型与具体角色承担目前容易混用，需结合 RoleAssignment 审议。
Organization | 以组织或团队身份存在的主体；成员关系和通信关系分别建模 | 有明确边界和成员关系的组织、团队或编组 | 组织模型文档、指挥设施和成员清单载荷不是组织本体 | 假设由若干数据审查主体组成的团队 | 描述该团队结构的组织模型 | OrganizationModel | 集体身份、成员变更和上下级关系需可追踪且与通信拓扑独立。
RoleAssignment | 将主体、角色及任务组织语境实体化；带有效时间的承担关系 | 某 Agent 在指定语境中承担某角色的关系 | 被分配的角色、分配命令信息和 Agent 主体分别具有身份 | 假设 Agent 在某段时间承担审查角色的关系 | 审查角色本身 | AgentRole | 组织、任务是否必填以及多角色冲突条件需领域裁决。
PlatformBinding | 在有效时段连接 Agent 与物理平台；绑定关系不使二者同一 | 指定运行或时间中 Agent 的物理实现/承载关系 | 平台类型、软件进程驻留记录和 Agent 身份不自动等价于绑定 | 假设 Agent 在某时段与某物理平台的承载关系 | 被绑定的物理平台 | Platform | 一对多、多对一和迁移期间的重叠绑定规则待定义。
Observation | 从局部传感、消息或环境获得的信息内容；来源与观测时间可追踪 | 具有观测语境的局部输入记录 | 推断信念、全局真值和实际观测行为需要分别表示 | 假设设备输出的一条温度观测内容 | 根据多条观测推断出的内部状态断言 | BeliefAssertion | 原始测量、转述消息和经处理观测的来源层次及质量标记需细分。
BeliefState | 某一主体在有效时段维护的信念集合；集合与单条信念断言区分 | 具有主体和时间范围的内部认知内容集合 | World 的外部事实库或全体主体共享真值不自动等于此状态 | 假设 Agent 当前维护的一组可追溯信念 | 其中单独一条带置信度的断言 | BeliefAssertion | 版本、撤回、矛盾共存和集合快照一致性需要明确。
BeliefAssertion | 是一条关于对象或命题的信念内容；声明主体、来源、置信度和有效时间 | Agent 对某命题所持的可追踪断言 | 原始观测、外部真值和不注明主体的通用文本不是等价对象 | 假设 Agent 基于观测形成的一条设备可用性判断 | 设备实际具有的可用性质量 | Quality | 撤回与修订不能覆盖历史依据；命题同一性与来源不足状态待明确。
Goal | 表达期望达到的状态；目标内容区别于承诺和实际执行 | Agent 或组织期望达成的条件信息 | 达成过程、已发生结果和执行授权不是目标本身 | 假设 Agent 希望资料质量达到某种要求的目标表述 | 实际进行资料检查的一次过程 | Task | 目标满意条件、优先级和冲突解决仅为描述层，需明确何时形成意图。
Task | 按现有 Process 父类指称执行活动；任务内容规范与其发生区分 | 可分配并在时间中实际开展的任务过程 | 尚未执行的任务说明、目标文字和流程模板是信息对象 | 假设的一次实际资料核对任务过程 | 描述资料核对步骤的任务说明 | ProcessSpecification | 历史“可执行/待分配任务”是否表示计划对象而非发生过程仍需逐实例审议。
Plan | 提供达到目标的步骤和选择条件；资源预算属于计划内容 | 可审查的行动或处理过程安排信息 | 已发生的过程、目标本身与执行授权不是计划内容 | 假设一份资料复核步骤与备选分支的安排 | 按安排实际进行的一次复核过程 | Task | 计划版本、计划采纳和实际偏离需分开记录。
Decision | 是主体依据证据作出的选择内容；选择与执行分离 | 具有决策主体和依据的选择记录 | 备选草案、对外授权及实际动作均不自动等同于此决定 | 假设 Agent 选择先核对来源的一条决策内容 | 仍供人审查、尚未采纳的推荐草案 | DecisionDraft | 自动选择、人类决定与授权裁决之间的关系必须保持显式，避免含混通用字段。
ActionRequest | 以请求内容指向某项动作或其授权；请求者不因此拥有执行权 | 某主体提出的、可以审核的动作请求信息 | 实际动作、已签发授权和人类审批请求分别建模 | 假设 Agent 请求启动一次资料校验的记录 | 真正开始进行的那次资料校验过程 | ActionExecution | 请求重试、取消及与审批请求的一对多关系需统一标识和状态规则。
Action | 表示 Agent 或平台实际实施的过程；行为类别规范不是此发生过程 | 已发生或正在发生的主体行为 | 动作请求、流程规范以及所报告的效果是不同类别 | 假设 Agent 实际读取一份资料的过程 | 请求读取该资料的消息内容 | ActionRequest | Action 与 ActionExecution 的一般/具体边界及未受请求驱动行为需审议。
Effect | 按现有 InformationEntity 父类表示效果报告；区分报告与实际因果结果 | 描述状态变化、产物或失败的结果信息 | 实际变化过程、质量自身和因果证明不由该记录自动成立 | 假设一条报告校验完成并发现缺字段的效果内容 | 资料被实际修改的过程 | Process | 结果归因、可验证性与观测佐证应分别表示，不能用报告替代因果证据。
Message | 可交换的信息载荷；内容与一次发送或接收行为区分 | 有生产者、接收对象和时间语境的消息内容 | 通信过程、协议规范及链路状态都不是消息载荷 | 假设 Agent 发出的状态报告内容 | 将该报告传送给另一主体的过程 | CommunicationAct | 同一载荷的多次投递、消息身份与传输封套身份需要明确。
CommunicationAct | 是发送、接收、确认或拒绝消息的发生过程；参与者和时间可区分 | 一次具体通信行为 | 消息内容、协议规范和端点关系不等于行为本身 | 假设主体实际接收一条消息的过程 | 被接收的消息内容 | Message | 发送与接收是否分成不同事件、失败和重试如何追踪需审议。
Protocol | 描述消息、顺序和失败语义；规范不保证实际互通成功 | 交换格式、确认、时序和失败处理的约定 | 某次消息、某次通信过程或链路拓扑不是协议 | 假设两个组件约定的消息确认规范 | 一次实际确认消息的接收过程 | CommunicationAct | 协议版本、兼容声明和可执行契约应有独立证据。
LinkState | 依存于特定链路及有效时间；描述通断、延迟、带宽等状态 | 某链路在明确时刻或区间具备的品质 | 链路身份、观察该状态的记录以及通信需求都需分开 | 假设链路在某时刻处于不可用状态 | 报告该不可用状态的一条观测 | Observation | 状态质量与测量断言分离后，过期、未知和不可用应分别编码。
Capability | 条件满足时可实现的能力；依存于承担能力的主体 | 某主体或设备具有的可实现能力 | 能力描述文档、已执行行为和授权权限不是能力本身 | 假设软件主体具备读取指定格式资料的能力 | 一次已经发生的资料读取过程 | Action | 能力、服务接口和容量资源之间的形式边界及失效条件需审议。
Policy | 具有版本和生效范围的规范内容；约束决策、行为或权限 | 用于明确范围的规则或策略集合 | 具体权限范围、单次授权结果和执行过程不是策略本身 | 假设关于资料访问条件的一份版本化策略 | 针对一次资料读取请求的授权结果 | Authorization | 策略优先级、冲突和更新生效时刻需显式，不能被学习结果自行覆盖。
Authority | 描述主体可代表的范围；广义权限边界区别于一次授权 | 对动作、对象、资源或空间的允许代表范围描述 | 具备能力、提出请求或收到一次批准不等于持有该权限范围 | 假设某资料审查主体可处理的资料范围说明 | 该主体对一次请求签发的具体授权结果 | Authorization | 委托链、范围缩减、撤销和权限证据需要统一表示。
Authorization | 是针对请求在规则范围内形成的裁决信息；必须可追溯到规则版本和评估依据 | 对一个明确请求所作的允许、拒绝或条件性结果 | 人工同意、检索文本、旧 Permit 产品和实际动作都不自动等价 | 假设对一次资料读取请求形成的有限有效期裁决 | 人类对同一请求作出的同意意见 | HumanDecision | 签发、撤销、过期与请求身份的关联需真实数据补证；不能假造缺失授权。
Run | 界定一次仿真或任务运行的发生范围；关联本次主体、状态和事件 | 有独立运行身份和边界的一次发生事项 | 模型版本、运行配置文件和批次组织记录不是运行本身 | 假设的一次有明确起止的资料流程演练 | 描述该演练参数的配置文档 | InformationEntity | 尚缺源数据中的若干 Run 实体，不能通过字符串引用自动制造其存在。
AuditEvent | 按现有 Occurrent 父类指称可审计的发生事项；发生与其回放记录应有独立身份 | 对观察、判断或操作发生本身的审计指称 | 序列化日志载荷是事件记录，不因可回放而自动等同发生体 | 假设的一次资料审批决定形成事项 | 描述该事项的可重放 JSON 日志内容 | Event | API 当前 event-sourcing 对象表示记录还是发生尚未裁决；不得据序列化形式强改层级。
EmbodiedAgent | 具有明确物理平台实现接口；Agent 与承载平台分别有身份 | 通过物理实现接收观测或作用接口的主体 | 平台本身、仅有硬件名称的目录和平台类型都不自动构成主体 | 假设通过设备平台实现局部观测接口的 Agent | 该 Agent 所绑定的物理平台 | Platform | 与 SoftwareAgent 允许交叠还是互斥必须明示，不应凭中文直觉声明不相交。
SoftwareAgent | 由软件运行实现决策与交互边界；具有主体状态而非仅静态算法 | 符合 Agent 定义的软件实现主体 | 静态模型文件、一次问答会话和无主体状态的函数不是自动等价对象 | 假设长期维护资料审查状态的软件主体 | 某操作员与助手的一次问答会话 | AssistantSession | 软件进程重启、模型替换与 Agent 身份延续的判据需要明确。
Desire | 属于目标内容但尚未形成执行承诺；允许同时存在多个候选期望 | 主体考虑但未承诺追求的目标表述 | 已选承诺、具体授权或实际任务过程不是未承诺愿望 | 假设 Agent 考虑改善资料完整度的候选目标 | Agent 已承诺执行的某项处理意图 | Intention | 与 Goal 的状态划分及意图形成条件需要能追溯而非仅词面区分。
Intention | 表达主体对目标或方案的承诺；承诺信息区别于授权和发生 | 已采纳并承诺追求的认知内容 | 未采纳的候选愿望、任务执行和权限许可不是同一对象 | 假设 Agent 承诺优先完成来源核对的内容 | 尚未采纳的改善资料质量愿望 | Desire | 承诺建立、保持、撤回和失败后的状态转移仍需明确。
StateTransition | 实际发生使状态前后不同；转移规范与发生实例区分 | 明确运行和时间中的状态变化过程 | guard 条件、状态图箭头和模板转移规则是规范内容 | 假设服务实际由等待变为处理状态的过程 | 状态机中“收到输入则开始处理”的规则 | ProcessSpecification | 前后状态质量与其断言的引用、触发证据和转移身份应分开。
Resource | 按现有父类限定为可分配或使用的独立对象；对象与可用量分离 | 在明确范围中可占用的独立资源对象 | 资源数量、能力、预算记录和分配关系不因此成为独立对象 | 假设可供资料校验任务占用的一台计算设备 | 记录设备可用容量的数值内容 | InformationEntity | 非物质资源是否满足 IndependentContinuant，需逐类别审议而非广泛套用。
Constraint | 限定可接受状态、行为或交换内容；规范与检查结果分离 | 具有适用主体或范围的限制内容 | 一次检测失败、规则执行器或受限对象本身不是约束 | 假设要求资料记录必须保留来源的约束内容 | 某条资料因缺少来源而失败的检查报告 | InformationEntity | 与 Rule、Invariant、Policy 的包含或并列关系未充分形式化。
AgentCycle | 一次可界定起止的主体运行周期；包含具体阶段而非模板步骤 | Agent 实际完成的一轮观测与处理等过程 | 周期模型、阶段规范和日志内容不等于周期本体 | 假设主体实际完成的一轮读取与校验过程 | 描述该循环阶段的一份模板 | ShellTemplate | 周期边界、重入、并发和跨时间尺度嵌套规则仍需明确。
TimeInterval | 以时钟、单位和边界限定时间范围；区间不是时间戳字符串 | 某明确时钟上的有界或显式开放区间 | 无历元锚点的仿真 tick 不可自动当作 UTC 日期时间 | 假设仿真时钟上一段注明 tick 单位的有效区间 | 把未锚定 tick 数直接格式化为 UTC 的日期字符串 | InformationEntity | 开闭端点、未知终点、零时长及跨时钟换算需明确且不补造单位。
ActionExecution | 是某请求在运行中的具体实现过程；与请求和授权裁决分离 | 可追踪执行主体、运行和实际结果的过程 | 授权记录、计划承诺和完成消息仅描述或允许执行，不就是执行 | 假设在校验请求之后实际发生的资料检查过程 | 允许进行资料检查的一份授权内容 | Authorization | 已有安全边界字段是运行约束，不能被误作物理过程的固有属性；缺授权应报缺口。
ProcessSpecification | 描述输入输出、前提、步骤和约束；规范不证明发生 | 可重复引用的过程描述内容 | 该规范的一次执行、执行日志和执行主体分别建模 | 假设一份资料校验流程说明 | 按说明实际进行的一次校验过程 | Process | Stage 和 Task 旧目录的规范部分如何迁移到此类需保持 IRI 与版本可追溯。
AgentExecution | 是某 Agent 在 Run 中的具体执行过程；主体与执行分离 | 有运行及时间边界的一段主体活动 | AgentInstance 主体、AgentType 规范和过程日志不是执行本身 | 假设某资料审查 Agent 的一段实际工作过程 | 进行该工作的 Agent 主体 | AgentInstance | 生命周期与执行段、暂停恢复和多并发执行的关系尚需正式约束。
AgentState | 依存于 Agent 的有效时间质量；状态品质与状态断言分离 | 某个主体在明确时刻或区间所具有的状态 | 含版本和来源的状态断言属于信息对象，其元数据不自动归质量本体 | 假设 Agent 当前具有的等待状态 | 记录该等待状态的一条带版本断言 | BeliefAssertion | 当前定义第二句涉及断言，需在 schema 与 UI 明确版本/来源归断言还是品质。
AssistantSession | 是人机问答的会话发生范围；助手上下文不等于运行主体状态 | 某操作员与助手开展的一次有边界交互 | AgentInstance、事实断言及授权结果不因出现在聊天中而产生 | 假设操作员查询知识库的一次问答会话 | 同期维护运行状态的 Agent 主体 | AgentInstance | 会话、操作员及被代表 Agent 的上下文所有权和记忆隔离需持续验证。
Operator | 指称使用助手的实际人类主体；人身份与会话、职位或决定区分 | 通过交互界面查询或提交请求的人 | 助手软件、用户账户记录和人承担的角色不是人本体 | 假设一位通过界面查询资料的人类操作员 | 该人的账户资料记录 | InformationEntity | 人、账户和组织授权角色的身份映射及匿名化边界需明确。
DecisionDraft | 尚待审查的方案推荐信息；草案不等于采纳或授权 | Agent 生成的可比较方案及其依据内容 | 已采纳选择、正式授权以及实际行为都不是草案 | 假设建议先核验某批资料来源的推荐草案 | 主体已采纳这一建议后的决策记录 | Decision | 草案修订、引用证据和采纳对应关系应可追踪，不可通过 UI 状态伪升级。
ApprovalRequest | 对明确动作请求提出人工审查要求；请求人工审查不构成授权 | 提交给有权人类审查的请求信息 | 人工决定、授权结果和原始动作请求语义分别存在 | 假设要求操作员审查一次资料访问请求的表单内容 | 操作员对此作出的同意或拒绝决定 | HumanDecision | 审批对象版本、撤回及重复审批应有稳定引用，避免旧批准误用。
HumanDecision | 人类对审批请求作出的可追踪意见；必须与机器授权结果分离 | 关联人类主体、审批对象和证据的决定内容 | 正式 Authorization、Agent 自己的选择或未提交草案不是人工决定 | 假设操作员对资料访问审批作出的同意意见 | 后续规则评估生成的授权结果 | Authorization | 人的身份、权限证据和决定有效范围需真实记录；不能把按钮点击直接作为充分授权。
TriggerDefinition | 描述事件条件、目标主体和调度约束；触发规范不授予动作权限 | 可配置的触发条件与调度规则内容 | 触发事件本身、一次调度过程和执行授权不等于定义 | 假设“收到新资料时请求校验”的触发器规范 | 某次真实收到新资料的发生事项 | AuditEvent | 条件语言、去重、重放和触发目标版本的语义应明确；触发后的请求仍需独立验证。
"""


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def digest(name):
    return hashlib.sha256((ROOT / name).read_bytes()).hexdigest()


def draft_rows():
    result = {}
    for line in REVIEW_ROWS.strip().splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) != 8:
            raise ValueError(f"expected 8 cells, got {len(cells)}: {line}")
        ident, features, included, excluded, positive, negative, alternatives, question = cells
        if ident in result:
            raise ValueError(f"duplicate draft {ident}")
        result[ident] = dict(
            distinguishingFeatures=features.split("；"),
            boundary={"includes": included, "excludes": [excluded]},
            positive=positive,
            negative=negative,
            alternatives=[x.strip() for x in alternatives.split(",") if x.strip()],
            question=question,
        )
    return result


def main():
    base, ext, reg = map(read, ["tbox.json", "agent.extension.json", "identity-registry.json"])
    drafts = draft_rows()
    definitions = [c for doc in (base, ext) for c in doc["classes"]]
    byid = {c["id"]: c for c in definitions}
    class_iris = {c["id"]: c.get("iri", B + c["id"]) for c in definitions}
    if set(drafts) != set(byid):
        raise ValueError({"missing": sorted(set(byid) - set(drafts)), "extra": sorted(set(drafts) - set(byid))})
    if len(byid) != len(definitions):
        raise ValueError("ambiguous local class IDs; resolve by full IRI before drafting")
    citations = {s["id"]: s for s in ext["provenance"]["sources"]}
    candidates = defaultdict(list)
    for record in reg["entries"]:
        if record["kind"] == "Individual":
            candidates[record["type"]].append({
                "id": record["id"], "iri": record["iri"], "file": record["file"],
                "role": "existing-source-record-candidate-only",
                "classificationReview": "pending; source typing is evidence to examine, not an approved example",
            })
    disjoint = defaultdict(list)
    for left, right in base.get("disjoint", []):
        disjoint[left].append(class_iris[right]); disjoint[right].append(class_iris[left])
    rows = []
    for filename, doc in [("tbox.json", base), ("agent.extension.json", ext)]:
        for index, cls in enumerate(doc["classes"]):
            ident, draft = cls["id"], drafts[cls["id"]]
            parent_iris = [p if p.startswith("http") else class_iris[p] for p in cls.get("subClassOf", [])]
            source = {"kind": "project-class-definition", "file": filename,
                      "jsonPointer": f"/classes/{index}", "sha256": digest(filename),
                      "scope": "current project definition and parent declarations; not external certification"}
            sources = [source, {"kind": "authored-conceptual-review", "file": "draft_class_definition_review.py",
                       "locator": f"REVIEW_ROWS/{ident}", "sha256": digest("draft_class_definition_review.py"),
                       "scope": "individually authored explanatory examples and boundaries; pending domain review"}]
            for key in ["equivalentTo", "seeAlso"]:
                for iri in cls.get(key, []):
                    sources.append({"kind": "existing-external-reference", "iri": iri,
                                    "projectClaim": key, "scope": "inherited citation only; exact source text/version and equivalence not newly verified"})
            prov = cls.get("provenance")
            if prov in citations:
                cite = citations[prov]
                sources.append({"kind": "existing-theory-reference", "id": prov, "iri": cite["iri"],
                                "scope": cite["use"] + "; project synthesis, not a quoted canonical class definition"})
            elif prov:
                sources.append({"kind": "local-research", "file": "agent-modeling-research.md", "scope": "project synthesis; no domain approval inferred"})
            if cls.get("definitionSource"):
                sources.append({"kind": "existing-definition-provenance", "value": cls["definitionSource"],
                                "scope": "project interpretation; semantic decisions do not replace domain validation"})
            questions = [draft["question"]]
            if cls.get("equivalentTo"):
                questions.append("既有 equivalentTo 外部映射需核对固定版本与定义必要充分条件；本审议草案不确认该等价成立。")
            if candidates[ident]:
                questions.append("已列出的源记录仅为待核验候选；应逐条核对其所指对象与本类定义，不能仅因 type 字段相同而通过。")
            if not candidates[ident]:
                questions.append("当前身份登记中没有本类直接个体候选；概念说明不填补真实数据覆盖缺口。")
            rows.append({
                "classIri": class_iris[ident], "id": ident, "label": cls.get("label", ident),
                "parentClasses": [{"iri": iri, "id": iri.rsplit("#", 1)[-1],
                                   "label": byid.get(iri.rsplit("#", 1)[-1], {}).get("label")} for iri in parent_iris],
                "definition": cls["comment"], "definitionSource": source,
                "distinguishingFeatures": draft["distinguishingFeatures"],
                "boundary": {**draft["boundary"], "directDisjointClassIris": disjoint[ident],
                             "status": "explanatory-draft; does not add OWL restrictions or new disjointness"},
                "positiveExamples": [{"kind": "illustrative-concept-example", "description": draft["positive"],
                    "whyFits": draft["boundary"]["includes"], "recordedInstance": False,
                    "assertsRealWorldOccurrence": False, "status": "pending-domain-review"}],
                "negativeExamples": [{"kind": "illustrative-concept-example", "description": draft["negative"],
                    "whyExcluded": draft["boundary"]["excludes"][0],
                    "alternativeClassIris": [class_iris[k] for k in draft["alternatives"]],
                    "recordedInstance": False, "assertsRealWorldOccurrence": False,
                    "status": "pending-domain-review"}],
                "sources": sources, "sourceIndividualCandidates": candidates[ident],
                "reviewStatus": "draft-pending-domain-review", "approvedBy": None,
                "reviewKind": "engineering-synthesis-of-current-source-and-explicit-conceptual-boundaries",
                "openQuestions": questions,
                "implementationEffect": "documentation-only; no new individuals, class retyping, formal restrictions or external-equivalence approval",
            })
    counts = {"classes": len(rows), "withParentDeclaration": sum(bool(r["parentClasses"]) for r in rows),
              "rootClasses": sum(not r["parentClasses"] for r in rows),
              "withDefinition": sum(bool(r["definition"]) for r in rows),
              "withDistinguishingFeatures": sum(bool(r["distinguishingFeatures"]) for r in rows),
              "withBoundary": sum(bool(r["boundary"]["includes"]) and bool(r["boundary"]["excludes"]) for r in rows),
              "withPositiveConceptualExample": sum(bool(r["positiveExamples"]) for r in rows),
              "withNegativeConceptualExample": sum(bool(r["negativeExamples"]) for r in rows),
              "withSourceReferences": sum(bool(r["sources"]) for r in rows),
              "withSourceIndividualCandidates": sum(bool(r["sourceIndividualCandidates"]) for r in rows),
              "domainApproved": 0, "statuses": dict(Counter(r["reviewStatus"] for r in rows))}
    output = {
        "reviewVersion": "1.0.0", "package": "coast-raid-k", "createdAt": datetime.now(timezone.utc).isoformat(),
        "title": "98 类定义、区别特征、边界与概念样例审议草案",
        "status": "complete-draft-coverage; domain-approval-open",
        "sourceHashes": {name: digest(name) for name in ["tbox.json", "agent.extension.json", "identity-registry.json", "semantic-decisions.json", "draft_class_definition_review.py"]},
        "scope": "All current 98 classes. Individually authored conceptual review; not domain approval or standard certification.",
        "examplePolicy": {"kind": "illustrative-concept-example", "realInstances": False,
            "addsABoxRecords": False, "provesValidationCoverage": False,
            "note": "正反例均为用于审议概念边界的假设性说明，不是真实运行实例、形式逻辑反模型或已通过的验证用例。"},
        "decisionPolicy": "保持现有 IRI 与父类。类别冲突或旧实例歧义记录为待裁决问题；草案不自动迁移实例、扩展权限或修改运行状态。",
        "counts": counts, "classes": rows,
    }
    target = ROOT / "class-definition-review.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(counts, ensure_ascii=True))


if __name__ == "__main__":
    main()
