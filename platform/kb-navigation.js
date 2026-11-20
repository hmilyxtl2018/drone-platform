// Navigation projection for the knowledge-base UI.
//
// This file deliberately does not change rdfs:subClassOf or any ontology
// axiom. It gives users a task-oriented projection of the TBox classes while
// keeping the formal hierarchy in tbox.json and agent.extension.json as the
// semantic source of truth.

export const CONCEPT_GROUPS = [
  {
    id: "foundation",
    label: "本体基础",
    hint: "上位本体、元模型与包级声明",
    ico: "onto",
    sections: [
      {
        id: "upper",
        label: "上位本体",
        hint: "BFO 风格的实体、持存体、发生体与关系体",
        classes: [
          "Entity",
          "Continuant",
          "Occurrent",
          "IndependentContinuant",
          "DependentContinuant",
          "MaterialObject",
          "SpatialRegion",
          "InformationEntity",
          "Quality",
          "Role",
          "Relator",
          "Process",
        ],
      },
      {
        id: "meta",
        label: "本体构件",
        hint: "类、包和排除项等规范层对象",
        classes: ["Class", "Package", "Exclude"],
      },
    ],
  },
  {
    id: "battlefield",
    label: "战场与环境",
    hint: "空间、作战设备以及它们之间的场景关系",
    ico: "scene",
    sections: [
      {
        id: "space",
        label: "空间与包络",
        hint: "岸线、海陆域、走廊和探测/干扰空间",
        classes: [
          "Shoreline",
          "Region",
          "Envelope",
          "Corridor",
          "SearchSector",
          "DetectionDisk",
          "JamBloom",
        ],
      },
      {
        id: "equipment",
        label: "设备与平台",
        hint: "指挥所、传感器、射手、平台及其型谱",
        classes: ["CommandPost", "Sensor", "Shooter", "Platform", "Emitter", "PlatformType"],
      },
      {
        id: "relations",
        label: "场景关系",
        hint: "隶属、岸上数据链和通信链路关系",
        classes: ["Membership", "Needline", "Link"],
      },
    ],
  },
  {
    id: "agents",
    label: "作战 Agent",
    hint: "七类作战职责与运行对象：世界、己方、方案、门控、执行和复盘",
    ico: "agent",
    // These seven sections are a responsibility projection for warfare
    // agents. They are deliberately orthogonal to rdfs:subClassOf: Goal,
    // Desire and Intention remain reusable planning concepts under Options;
    // an explicit Adversary model is still pending. Human review and
    // permission stay in Gate.
    sections: [
      {
        id: "world",
        label: "01 World｜世界模型与输入",
        hint: "回答“现在认为发生了什么”：观测、信念和可追溯的消息证据；不直接生成决心",
        classes: [
          "Observation",
          "BeliefState",
          "BeliefAssertion",
          "Message",
          "CommunicationAct",
          "Protocol",
        ],
      },
      {
        id: "self",
        label: "02 Self｜己方状态与身份",
        hint: "回答“自己还能做什么”：主体、组织、平台、链路、资源、能力和当前状态",
        classes: [
          "Agent",
          "AgentInstance",
          "AgentFunction",
          "OperationalTimeScale",
          "AgentClass",
          "AgentType",
          "AgentRole",
          "EmbodiedAgent",
          "SoftwareAgent",
          "OrganizationModel",
          "Organization",
          "RoleAssignment",
          "PlatformBinding",
          "LinkState",
          "Capability",
          "Resource",
          "AgentState",
        ],
      },
      {
        id: "adversary",
        label: "03 Adversary｜对手意图与适应",
        hint: "对手模型待建模：当前不把通用 Goal / Desire / Intention 误标为对手事实",
        classes: [],
      },
      {
        id: "options",
        label: "04 Options｜方案与推演",
        hint: "只产可执行选项：目标、任务、计划、决策和过程规范；附带代价、窗口和失败回退，不持有发射权",
        classes: ["Goal", "Desire", "Intention", "Task", "Plan", "Decision", "DecisionDraft", "ProcessSpecification"],
      },
      {
        id: "gate",
        label: "05 Gate｜约束、授权与人在环",
        hint: "回答“这一条过不过”：策略、约束、权限范围与授权裁决；保留助手会话和操作员的责任边界",
        classes: ["Policy", "Authority", "Authorization", "ApprovalRequest", "HumanDecision", "Constraint", "AssistantSession", "Operator"],
      },
      {
        id: "effectors",
        label: "06 Effectors｜效果执行与中止",
        hint: "真正接触物理世界：动作请求、执行、效果和状态转换；每个执行面应有短授权寿命与中止条件",
        classes: ["ActionRequest", "Action", "ActionExecution", "AgentExecution", "Effect", "StateTransition"],
      },
      {
        id: "learn",
        label: "07 Learn｜运行轨迹与战后复盘",
        hint: "只收集轨迹和错误：运行、周期、时间与审计事件；更新模型先验，不直接改 Gate 红线",
        classes: ["Run", "AuditEvent", "AgentCycle", "TimeInterval"],
      },
    ],
  },
  {
    id: "methods",
    label: "规则与算法",
    hint: "行为规则、计算算法和门控/职能绑定",
    ico: "algo",
    sections: [
      {
        id: "governance",
        label: "规则、算法与职能",
        hint: "可执行规则、算法实现以及监视和职能角色",
        classes: ["Rule", "Algorithm", "FunctionBind", "Monitor"],
      },
    ],
  },
  {
    id: "products",
    label: "信息产品",
    hint: "交战过程中产生、交换和评估的运行产品",
    ico: "fight",
    sections: [
      {
        id: "engagement-products",
        label: "交战产品",
        hint: "航迹、身份、配对、槽位、许可和波次等产品",
        classes: ["Track", "Identity", "Pair", "Slot", "Permit", "Wave", "Channel", "Inject", "Event", "Invariant"],
      },
    ],
  },
  {
    id: "execution",
    label: "运行与追踪",
    hint: "运行边界、Agent 周期、状态转换和审计回放",
    ico: "rule",
    sections: [
      {
        id: "runtime-trace",
        label: "运行生命周期",
        hint: "面向项目壳体的阶段与模板定义；Agent 运行、周期和审计归入 Learn 子组",
        classes: ["Stage", "ShellTemplate", "TriggerDefinition"],
      },
    ],
  },
  {
    id: "context",
    label: "知识与助手",
    hint: "知识定义以及作战、Agent 局部和助手会话上下文",
    ico: "know",
    sections: [
      {
        id: "contexts",
        label: "知识与上下文主体",
        hint: "CloudDef 知识定义；Operator 与 AssistantSession 作为 Gate 的人在环边界",
        classes: ["CloudDef"],
      },
    ],
  },
];
