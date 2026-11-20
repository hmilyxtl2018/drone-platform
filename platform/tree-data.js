/** Shared catalog for 交战本体 panel. */

export const PAGES = {
  kb: { href: "./kb.html", name: "知识库" },
  space: { href: "./space.html", name: "岸海态势" },
  field: { href: "./field.html", name: "Ontology based Agent 作战协同" },
  cop: { href: "./cop.html", name: "共同图" },
  swarm: { href: "./swarm.html", name: "球面码" },
};

export const META = {
  see: {
    role: "写入航迹",
    eat: "走廊、舰探、岸搜的回波与覆盖。",
    give: "航迹。交给识别，不直接开门。",
    cut: "金线断：物理包络还在，队级航迹不再融合，各站只用局部。",
    field: { page: "field", look: "走廊里的红航迹，以及岸上数据链通还是断。", click: "演" },
  },
  read: {
    role: "写入身份",
    eat: "航迹，加上金线是否还通。",
    give: "敌 / 我 / 不明，以及企图进哪一口泡。",
    cut: "金线断：依据变局部，不明不进交战清单，也不能当队级真值去授权。",
    field: { page: "cop", look: "情报抽屉与身份列。黄四叶是不明。", click: null },
  },
  assign: {
    role: "写入盯梢",
    eat: "身份、剩余通道 m、中空还剩几架。",
    give: "谁盯谁。盯梢线交给编队去飞。",
    cut: "断链后局部认领；通道满是饱和，不是加打的理由。",
    field: { page: "field", look: "青色协同线：我机连到红批。点平台看写入。", click: "演" },
  },
  form: {
    role: "写入阵位",
    eat: "该层活着的平台，以及分配到的阵位任务。",
    give: "同层阵位与 ε。跨层只传信念，不混编。",
    cut: "减员只重算该层。金线断了，各层仍按自己的码收，不混编。",
    field: { page: "swarm", look: "三套球面码。底栏可单看一层。", click: "解说" },
  },
  gate: {
    role: "写入许可",
    eat: "是否在 SAM 区内、p、身份、通道 m。",
    give: "过门 / 不过门。只给许可条件，不开对人自动交战。",
    cut: "拒止时沿上过不了门。饱和、p 不足、不明，都不开门。",
    field: { page: "cop", look: "处置列：待门、已许可、不打。", click: "演" },
  },
  foe: {
    role: "对抗（对方）",
    eat: "—",
    give: "有界注入：拒止、减员、下一波。",
    cut: "不是第七个我方角色。成功是注入后 N、ε、门仍在。",
    field: { page: "field", look: "点「拒止」「减员」「下一波」后，金线和编队是否还成立。", click: "拒止" },
  },
};

export const TREE = [
  {
    id: "see",
    title: "探测跟踪",
    hint: "监视、检测、航迹处理",
    output: "走廊/舰探/岸搜上的稳定航迹",
    algos: [
      {
        name: "检测器 + 恒虚警 CFAR",
        does: "从回波里判有无目标，并控制虚警率。",
        use: "走廊贴海、RCS 很小。虚警一抬，后面的授权门 τ 只能更严，不能用多打来补。",
        field: { page: "cop", look: "航迹表里新批、p 还在 0.5 附近的那些。", click: null },
      },
      {
        name: "卡尔曼 / EKF / UKF / IMM",
        does: "单批航迹滤波；IMM 在巡航与机动模型间切换。",
        use: "岸搜或舰探连续跟一批 Hxx。贴海段用巡航模型，压沿机动时再切。",
        field: { page: "space", look: "走廊里一条红航迹是否连续，不要断成两批。", click: null },
      },
      {
        name: "JPDA / MHT",
        does: "杂波和漏检下，把量测关联到多条航迹。",
        use: "第 k 波多批挤在走廊里，避免把两架拧成一条。",
        field: { page: "cop", look: "航迹清单批号是否一对一，有没有合并错。", click: "下一波" },
      },
      {
        name: "PHD / GLMB（随机有限集）",
        does: "目标数目未知时，同时估计数量和状态。",
        use: "新波次进走廊，先回答「这波有几批」。数量仍受 Nmax 卡住，不是无限蜂群。",
        field: { page: "space", look: "右上角 N 与 k。点「下一波」看批次增加是否被上限挡住。", click: "下一波" },
      },
      {
        name: "航迹融合 / 协方差交叉",
        main: true,
        does: "多平台、多传感器合成一条队级航迹。",
        use: "金线通：岸搜+舰载+制导并成队级航迹。拒止后只留局部，不能把局部 p 当授权真值。",
        field: { page: "space", look: "岸上数据链通/断。断了以后航迹还在，队级融合停。", click: "拒止" },
      },
    ],
  },
  {
    id: "read",
    title: "识别判断",
    hint: "敌我识别、威胁评估、意图",
    output: "是敌、是我、还是不明；要进哪一口泡",
    algos: [
      {
        name: "IFF 逻辑 / 分类器",
        main: true,
        does: "判敌、我、不明。混战里身份是核心不确定。",
        use: "黄菱形是不明：只识别，不进交战清单，也不进任何一层球面码。",
        field: { page: "cop", look: "情报抽屉与航迹表「身份」列。黄四叶不进处置「打」。", click: null },
      },
      {
        name: "贝叶斯网 / 影响图",
        does: "把通联、机动、电子信号连成可推断的态势。",
        use: "沿走廊贴海压向 SAM，后验从「在走廊」升到「企图进中层交战区」。",
        field: { page: "space", look: "批标：区内 / 区沿 / 走廊。意图随所在泡变。", click: "演" },
      },
      {
        name: "D-S 证据 / 冲突融合",
        does: "多源报告打架时，不硬投一个结论。",
        use: "岸搜像来袭、身份仍不明：保持冲突，交给授权层，不先打。",
        field: { page: "cop", look: "档案与「不明」同时存在。空隙写在情报摘要里。", click: null },
      },
      {
        name: "威胁评估（加权 / 效用）",
        does: "把多批目标排成跟踪或防护顺序。",
        use: "按距 SAM 内界排序，不是按谁好打。区沿跟踪，未进区不打。",
        field: { page: "cop", look: "航迹表距界列。越近内界越先盯梢。", click: null },
      },
      {
        name: "HMM / 机动意图识别",
        does: "从轨迹模式估计对方阶段。",
        use: "本场阶段是走廊 → 舰探 → 区沿 → 区内。阶段变了，分配和门才变。",
        field: { page: "space", look: "一条红航迹从海面走廊走进 SAM 半球。", click: "演" },
      },
    ],
  },
  {
    id: "assign",
    title: "任务分配",
    hint: "任务、火力、空域",
    output: "有限中空去盯谁；通道 m 不是开门理由",
    algos: [
      {
        name: "匈牙利算法 / 拍卖 / CBBA",
        main: true,
        does: "一对一或去中心化任务分配。",
        use: "中空有限架次对来袭批做盯梢/识别配对。金线通可用集中分配，断链后局部认领。",
        field: { page: "space", look: "青线：我机 → 红批。配对写在「交战本体」。", click: "演" },
      },
      {
        name: "武器—目标分配 WTA（MIP）",
        does: "有限弹药打多目标，最大化拦截。",
        use: "本场对应的是 SAM 通道 m。m=0 是饱和，饱和不能代替过门。",
        field: { page: "cop", look: "指挥抽屉里通道 m/M。满了只显示饱和，不开门。", click: null },
      },
      {
        name: "CBS / 优先级规划",
        does: "时空路径冲突消解，避免抢同一空域。",
        use: "低空都在走廊红盒子里，槽位错开，不抢同一格。",
        field: { page: "swarm", look: "低空码上各点是否挤成一团。", click: null },
      },
      {
        name: "信息增益贪心 / 传感器管理",
        does: "下一步去看最能降低不确定的地方。",
        use: "中环：泡沿 p≈½ 处加密观测。内环仍走球面码槽位，不拿信息增益当队形。",
        field: { page: "space", look: "SAM 区沿那一圈。沿上只跟踪，不打。", click: "演" },
      },
      {
        name: "动态再分配 / 滚动规划",
        does: "平台损失或新波次后重解分配。",
        use: "减员或下一波后，剩余中空在 N(E)≤Nmax 内重认领盯梢。不跨层把低空改去干中空的活。",
        field: { page: "space", look: "点「减员」后青线是否改挂到还活着的批。", click: "减员" },
      },
    ],
  },
  {
    id: "form",
    title: "编队协同",
    hint: "战术队形、一致性、协同",
    output: "三套有限球面码；一层一套",
    algos: [
      {
        name: "一致性 / 编队势函数",
        does: "连通时对齐速度、航向、相对位置。",
        use: "金线通，把散开的同层飞机收回该层槽位。断链后仍按层收，不混编。",
        field: { page: "swarm", look: "虚线：飞机到机位。点「演」看 ε 是否下降。", click: "演" },
      },
      {
        name: "有限立体构型 / 球面码槽位",
        main: true,
        does: "N 有上界时三维槽位可收敛，不是平面圆阵或无限扩群。",
        use: "高/中/低各一套码。低空压在走廊里。减员只重算该层。",
        field: { page: "swarm", look: "三套码。底栏「高 / 中 / 低」可单看一层。", click: "解说" },
      },
      {
        name: "ORCA / 速度障碍 / 势场避碰",
        does: "近距飞行时互不碰撞。",
        use: "走廊很窄时防相撞。本场主约束是槽位和红盒子，避碰是内环旁边的安全网。",
        field: { page: "swarm", look: "低空码里两点不要重合。", click: null },
      },
      {
        name: "共识滤波 / 延迟共享",
        does: "通信约束下融合局部信念。",
        use: "拒止：各高度层只信自己的局部 p。复通后再并队级信念。",
        field: { page: "space", look: "点「拒止」后情报变成「局部」。槽位仍按层收。", click: "拒止" },
      },
      {
        name: "MAPPO / HAPPO / QMIX",
        does: "规则写不全、部分可观测时学协同策略。是中环工具，不是本场权利要求。",
        use: "本场内环已用手写球面码。只有盯梢/压沿规则写不全时，才用 CTDE 学谁去看。不把侦打整场交给 MAPPO。",
        field: { page: "space", look: "中空盯梢是规则配对。学习只替换写不全的那一段。", click: null },
      },
    ],
  },
  {
    id: "gate",
    title: "交战授权",
    hint: "交战规则、射击许可、人在回路",
    output: "区内且 p≥τ 且身份允许；饱和不开门",
    algos: [
      {
        name: "置信门控（阈值 τ）",
        main: true,
        does: "p≥τ 才允许从跟踪转入许可。本场 τ=0.85。",
        use: "必须在 SAM 授权区内，且单批 p≥0.85。区沿、走廊、不明都不开门。",
        field: { page: "cop", look: "航迹表 p 与处置。区内 p 不足是「待门」，不是打。", click: "演" },
      },
      {
        name: "交战规则 RoE / 专家规则",
        does: "身份、空域、弹药类型的硬约束。",
        use: "不明、走廊外、未进内界：算法只能建议。人对人自动交战不进本问题。",
        field: { page: "cop", look: "黄四叶与「不打」。授权条件在指挥抽屉。", click: null },
      },
      {
        name: "约束 MDP / 机会约束",
        does: "把误打概率写成约束，而不是再加一条优化目标。",
        use: "宁可少覆盖，也不放开门。饱和、通道满，都不是松 τ 的理由。",
        field: { page: "cop", look: "通道 m=0 时处置仍是不打。", click: null },
      },
      {
        name: "杀伤链时序 / 射击门",
        does: "跟踪—瞄准—发射的时间窗口。",
        use: "本场射击门是 SAM 虚线内界以内。进半球但还在沿上，只跟踪。",
        field: { page: "space", look: "B_SAM 虚线内界。内界以内才谈过门。", click: "演" },
      },
      {
        name: "人在回路建议生成",
        does: "给出方案与约束，授权留在指挥员。",
        use: "算法给出过门条件。这几页演示里过门只占用通道，不开对人自动打击。",
        field: { page: "cop", look: "指挥六支「交战授权」。已许可 / 待许可。", click: null },
      },
    ],
  },
  {
    id: "foe",
    title: "对抗敌情",
    hint: "威胁环境、电子对抗、鲁棒注入",
    output: "拒止、减员、有限波次后，闭环仍停在不变集",
    algos: [
      {
        name: "矩阵博弈 / 极小极大",
        does: "小策略集上对冲对方最坏选择。",
        use: "本场来袭方向已被走廊约束。不必先求一个均衡再飞。",
        field: { page: "space", look: "红盒子指向岸上。波次沿走廊来，不是全图寻优。", click: null },
      },
      {
        name: "随机博弈 / POSG 近似",
        does: "双方都部分可观测时的对抗模型；精确解通常不可行。",
        use: "不求纳什。用有限波次和有界注入代替全场博弈。",
        field: { page: "cop", look: "k/K。波次用尽就不能再灌。", click: "下一波" },
      },
      {
        name: "I-POMDP / 对手建模",
        does: "把对方意图放进很浅的嵌套信念。",
        use: "意图来自所在泡：走廊突防、压沿、进区。层级必须浅。",
        field: { page: "space", look: "批旁意图字。拒止后依据变局部。", click: "拒止" },
      },
      {
        name: "鲁棒控制 / 最坏扰动",
        main: true,
        does: "不建模对手策略，把对抗当作有界注入。",
        use: "拒止、减员、新波次是注入。成功是 ε 仍有界、N 仍受限、门仍在。",
        field: { page: "swarm", look: "点「拒止」或「减员」后，同层码是否还在收。", click: "减员" },
      },
      {
        name: "多方博弈 / 身份切换规则",
        does: "混战里敌友会变，先管身份再谈均衡。",
        use: "不明先改 IFF 与门控，而不是重解全场分配。",
        field: { page: "cop", look: "不明批进入清单但不进「打」。", click: null },
      },
    ],
  },
];
