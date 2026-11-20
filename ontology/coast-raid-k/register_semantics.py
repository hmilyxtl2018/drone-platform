"""Register explicit interchange semantics; never builds or publishes a DB.

This is a source-maintenance generator.  Run deliberately after reviewing the
tables below.  It writes the vocabulary JSON, mapping registry and its Schema.
The conversion path does not infer meanings from arbitrary field names.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
B = "https://local.ontology/coast-raid-k#"
A = "https://local.ontology/coast-raid-k/agent/1.0.0#"
X = "http://www.w3.org/2001/XMLSchema#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
DCT = "http://purl.org/dc/terms/"

BASE_DEFINITIONS = {
 "IndependentContinuant":"独立持存体：在其存在期间具有自身身份、不作为另一实体的质量或角色而存在的持存对象。本项目的物理平台和空间对象按各自子类进一步限定。",
 "DependentContinuant":"依赖持存体：其存在依赖某个承载者的质量、角色或能力；不与承载它的物体混同。",
 "MaterialObject":"物质对象：在时间中保持身份并占据空间的物理实体；记录、模板和空间包络不因描述物体而成为物质对象。",
 "Process":"过程：在时间中发生、具有时间部分的活动或变化；过程规范和过程记录属于信息实体。",
 "Package":"知识包：汇集词汇、事实目录、约束、来源与版本声明的信息对象；包级单位与坐标约定不自动成为包内每个对象的属性断言。",
 "Exclude":"排除项：知识包对未建模或明确禁止纳入范围的功能/概念所作声明；不是场景物理对象。",
 "Shoreline":"岸线：在指定工程坐标框架内界定海陆分界的空间曲线。采样折线是原参数曲线的近似表示。",
 "Region":"区域：按边界、坐标框架或分类约定界定的空间范围；区域成员关系不表示占领或控制事实。",
 "Membership":"成员关系：明确记载某对象依据指定规则归属于某区域的关系体；关系本身与参与该关系的对象分别具有身份。",
 "Envelope":"空间包络：由参数定义的三维空间范围；用于描述覆盖或约束范围，不等同于产生该范围的设备或设备能力。",
 "Corridor":"走廊：具有边界和位置约定的通行或分析空间范围；其几何与实际对象是否位于其中是不同断言。",
 "SearchSector":"搜索扇区：由中心、半径、方向及高度等参数限定的空间区域；矩形外包框不是扇区本身。",
 "DetectionDisk":"探测盘：以中心与半径定义的圆盘空间范围；表示几何范围，不证明范围内目标已被探测。",
 "JamBloom":"干扰范围：对干扰影响空间所作参数化区域描述；不等于已发生的电磁效果或对效果确定性的保证。",
 "CommandPost":"指挥所：在场景中被登记为指挥设施的物质对象；软件 Agent、组织和授权角色分别建模。",
 "Sensor":"传感器：用于取得观测的物质设备；观测、感知算法和设备型谱分别表示。",
 "Shooter":"效果设备：场景中登记的效果实施物质设备；设备记录及其通道数不表示实际授权或已执行动作。",
 "Platform":"平台：可承载设备或 Agent 实现的物质对象；与平台型谱以及运行 Agent 实例分别具有身份。",
 "PlatformType":"平台型谱：规定一类平台的类别与描述特征的信息对象；inScene 仅记录该型谱在当前场景中的使用标记，不把型谱个体变成物质平台，也不表示某一平台或批次已实例化。",
 "Emitter":"发射设备：场景中登记的产生信号或辐射的物质设备；其状态、范围描述与具体发射过程分开表示。",
 "Needline":"信息需求关系：记录通信参与对象及其预期信息交换关系的关系体；不自动断言链路当前可用。",
 "Link":"通信链路关系：对参与对象、链路方式或拓扑关系的登记；通道、消息与具体通信过程分别建模。",
 "Invariant":"不变约束：在明确适用范围内要求持续成立的条件规范；是否满足需要针对输入和规则版本执行校验。",
 "Rule":"规则：以条件、模态和约束内容表达的规范信息对象；规则文本、机器执行器和某次检查结果是不同对象。",
 "Algorithm":"算法：可由实现执行的计算方法规范；名称或绑定不证明某次执行已发生，也不自动获得授权。",
 "CloudDef":"云模型定义：以期望、熵、超熵等数字特征描述不确定性模型的信息对象；其所表征的质量与该定义本身不同。",
 "ShellTemplate":"壳体模板：描述阶段、平台型绑定及读写契约的可复用运行规范；不是某次运行中的 Agent 实例。",
 "Monitor":"监视角色：在规则目录中承担监视与检查责任的角色对象；角色不等同于软件主体或某次授权裁决。",
 "FunctionBind":"职能绑定角色：某承载主体在指定组织或运行语境中承担的职能角色；输入、输出、约束及实现绑定描述该角色的履行条件，描述记录与角色本身分开。不因名称为 gate 而等同于 Rule/gate、授权规范或授权主体。",
 "Stage":"阶段：在一次具体运行中发生、并构成更大过程的有界过程片段；具有实际参与者与时间位置。壳体模板中的阶段名称、顺序及输入输出是阶段规范的描述，不凭模板记录断言该过程已经发生。",
 "Track":"航迹记录：对观察或估计的目标状态及其关联作出的信息表述；不等于被跟踪物体或无误差真值。",
 "Identity":"身份判断记录：对对象类别或归属的认定及其依据的信息表述；判断状态与实际对象身份不混同。",
 "Pair":"配对记录：对两个或多个参与对象之间候选/已选关联的表述；不是参与对象本身或执行结果。",
 "Slot":"资源槽记录：描述资源或时间安排的离散分配单元；记录存在不表示对应执行已发生。",
 "Permit":"项目许可记录：旧模型中的许可产品，表达本包检查结果；与 Agent Authorization 的映射需显式说明，不自动等价。",
 "Wave":"批次记录：描述一组有限任务或产品的组织单元；与真实过程的发生和完成状态分别登记。",
 "Channel":"通道记录：描述可用于信息或资源交换的通道及相关容量状态；不是消息内容或通信行为。",
 "Inject":"实验注入记录：描述向模型输入的受控变化或事件规范；不表示外部世界已发生同一变化。",
 "Event":"事件记录：对发生事项的时间、主体及载荷所作的信息表述；记录不是它所描述的世界过程。"
}

AGENT_DEFINITIONS = {
 "EmbodiedAgent":"具身 Agent：通过明确物理平台实现感知或作用接口的 Agent；Agent 身份与承载平台身份分别登记。",
 "SoftwareAgent":"软件 Agent：由软件运行实现、具有状态和交互边界的 Agent；所用模型或聊天会话不自动等于该主体。",
 "Desire":"愿望：Agent 尚未形成执行承诺的候选目标表述；多个愿望可并存，不自动构成任务或授权。",
 "Intention":"意图：Agent 对选定目标或方案所形成的承诺表述；实际过程和授权裁决分别登记。",
 "StateTransition":"状态转移：在指定运行中使状态由前态转为后态的过程；转移规范中的 guard 或触发器不是已发生的转移。",
 "Resource":"资源：在明确范围中可被占用、分配或使用的独立对象；资源可用量和分配记录与资源本身分别建模。",
 "Constraint":"约束：限定可接受状态、行为或交换内容的信息规范；适用范围与执行检查结果必须分别登记。",
 "AgentCycle":"Agent 周期：一次可界定起止与阶段组成的运行过程；周期模板、日志记录和实际周期不混同。",
 "TimeInterval":"时间区间：由明确时钟、单位及边界限定的时间范围；仿真数值时钟在没有历元锚点时不表示 UTC 日期时间。",
 "ActionExecution":"动作执行：在某次运行中实现动作请求的过程，引用具体执行主体、有效授权及实际结果；执行记录表述该过程，不与授权决定混同。",
 "ProcessSpecification":"过程规范：描述过程的输入、输出、前提、步骤和约束的信息对象；规范存在不意味着过程已经执行。",
 "AgentExecution":"Agent 执行：某个 Agent 在指定 Run 与时间范围中的具体运行过程；与 Agent 主体及其可复用模型分别具有身份。",
 "AgentState":"Agent 状态：在明确有效时间中依存于某个 Agent 的质量或状态；状态断言携带版本和来源，不自动代表客观真值。",
 "Task":"任务：在时间中可执行、可分配并具有完成状态的过程；任务描述和前提规范由 ProcessSpecification 表示。当前保持 Process 上位类。",
 "Effect":"效果记录：对动作所报告的状态变化、产品或失败结果的信息表述；不把该记录直接当成实际因果效果或发生过程。"
}

def read(name): return json.loads((ROOT/name).read_text(encoding='utf-8'))
def write(name,data): (ROOT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def update_vocabulary():
    base, ext = read('tbox.json'), read('agent.extension.json')
    for c in base['classes']:
        if c['id'] in BASE_DEFINITIONS:
            c['comment']=BASE_DEFINITIONS[c['id']]
            c['definitionStatus']='project-definition-pending-domain-review'
            c['definitionSource']='semantic-decisions.json; project source records'
            if c['id'] in ('FunctionBind', 'Stage', 'PlatformType'):
                c['definitionSource']='semantic-decisions.json decision D05; existing class hierarchy; project source records'
            if c['id']=='Event': c['label']='事件记录'
    for c in ext['classes']:
        if c['id'] in AGENT_DEFINITIONS:
            c['comment']=AGENT_DEFINITIONS[c['id']]
            c['definitionStatus']='project-definition-pending-domain-review'
            c['definitionSource']='semantic-decisions.json; '+c.get('provenance','localResearch')
            if c['id']=='Effect': c['label']='效果记录'
    # These names are distinct because the old predicate domains express
    # different subjects.  Never add two domains as a supposed union.
    obj = [
      ('usesAuthorization','ActionExecution','Authorization','执行过程引用本次使用的授权裁决。'),
      ('requestedBy','ActionRequest','Agent','提出动作请求的 Agent；与 requestsAction 互逆。'),
      ('requestIri',B+'Entity','ActionRequest','授权或执行记录所引用的具体动作请求。'),
      ('hasEffect','ActionExecution','Effect','执行过程所报告的效果信息对象；不隐含因果真值。'),
      ('decisionMaker','Decision','Agent','形成该决策记录的 Agent；与授权签发者分开。'),
      ('hasProvenanceRecord',B+'Entity',B+'InformationEntity','关联来源描述记录，不替代该记录中的来源证据。')
    ]
    dat = [
      ('executionResult','ActionExecution','xsd:string','执行结果；不同于 ag:result 授权决定。'),
      ('requestedAt','ActionRequest','xsd:dateTime','动作请求被提出的壁钟时间。'),
      ('executedAt','ActionExecution','xsd:dateTime','执行过程登记的壁钟时间。'),
      ('runModelId','Run','xsd:string','该运行采用的模型标识。'),
      ('runModelRevision','Run','xsd:string','该运行采用的模型版本。'),
      ('intervalFrom','TimeInterval','xsd:decimal','区间起点，依据 timeClock 与 timeUnit 解释。'),
      ('intervalTo','TimeInterval','xsd:decimal','区间终点，未给值时不推断无限远。'),
      ('timeClock','TimeInterval','xsd:string','区间使用的时钟标识；未登记时不可补造。'),
      ('timeUnit','TimeInterval','xsd:string','区间使用的计时单位；无来源时不可补造。'),
      ('sourceSha256',B+'InformationEntity','xsd:string','来源描述中记录的 SHA-256；存在值不表示本次已重算。'),
      ('hashStatus',B+'InformationEntity','xsd:string','来源哈希登记状态，不是本次校验结论。')
    ]
    for seq,key,isobj in [(obj,'objectProperties',True),(dat,'dataProperties',False)]:
        byid={p['id']:p for p in ext[key]}
        for ident,dom,ran,comment in seq:
            p={'id':ident,'iri':A+ident,'domain':dom if dom.startswith('http') else A+dom,
               'range':ran if ran.startswith(('http','xsd:')) else A+ran,'comment':comment,'provenance':'localResearch'}
            if ident=='requestedBy': p['inverseOf']=A+'requestsAction'
            if ident in byid: byid[ident].update(p)
            else: ext[key].append(p)
    for p in ext['dataProperties']:
        if p['id']=='authorizationIri':
            p['deprecated']=True
            p['comment']='旧 URI 字面量谓词，仅保留兼容声明；ActionExecution JSON.authorizationIri 现显式映射到对象关系 ag:usesAuthorization。'
    for ax in ext['axioms']:
        if ax['id']=='belief-has-provenance': ax['properties']=['assertedBy','derivedFrom','confidence','validDuring']
        if ax['id']=='action-execution-has-safety-envelope':
            ax['kind']='requiredProperties'; ax['properties']=['usesAuthorization','expiresAt','abortCondition','safeFallbackPolicy','reversibility','failureContainment']
    write('tbox.json',base);write('agent.extension.json',ext)
    return base,ext

def generate_mapping(base,ext):
    defs={}
    for f in read('catalog.json')['schemaModules']: defs.update(read(f).get('$defs',{}))
    classes={c['id']:c.get('iri',B+c['id']) for c in base['classes']+ext['classes']}
    def props(name,seen=None):
        seen=set(seen or ())
        if name in seen: return {}
        seen.add(name); d=defs.get(name,{})
        result={}
        for part in [d,*d.get('allOf',[])]:
            if '$ref' in part: result.update(props(part['$ref'].rsplit('/',1)[-1],seen))
            result.update(part.get('properties',{}))
        return result
    # Exact reviewed field tables.  Nothing below treats arbitrary $ref or
    # name suffixes as object-property evidence.
    relation={
      'AgentInstance':{'agentType':'realizesType','implementedOn':'implementedOn','organization':'memberOf','roles':'hasRole','capabilities':'hasCapability','authorityScope':'hasAuthority','performsFunction':'performsFunction','operatesAtTimeScale':'operatesAtTimeScale'},
      'Observation':{'assertedBy':'assertedBy','observedEntity':'observedEntity'},
      'BeliefAssertion':{'assertedBy':'assertedBy','aboutIri':'believesAbout','derivedFrom':'derivedFrom'},
      'Authorization':{'requestIri':'requestIri','decidedBy':'decidedBy','scope':'scope'},
      'ActionRequest':{'requestedBy':'requestedBy','authorizationIri':'authorizedBy'},
      'ActionExecution':{'requestIri':'requestIri','authorizationIri':'usesAuthorization','realizedBy':'realizedBy'},
      'Decision':{'decidedBy':'decisionMaker'},
      'AssistantSession':{'operatorIri':'initiatedBy','representedAgentIri':'representsAgent','requestIris':'submitsRequest'}
    }
    scalar={
      'AgentInstance':'modelId modelRevision decisionMode autonomyLevel lifecycleState functionDomain timeScale humanControlMode connectivityMode failureContainment safeFallbackPolicy reversibility authorizationTtlSeconds'.split(),
      'Observation':'observationKind sourceIri observedAt confidence'.split(),
      'BeliefAssertion':['confidence'],
      'Authorization':'result authorizationStatus reasonCode issuedAt expiresAt revocable revokedAt revocationReason ruleVersion policyVersion evidenceUris abortConditions safeFallbackPolicy reversibility failureContainment requiredHumanControlMode expiresAtTick'.split(),
      'ActionRequest':'requestedAt status failureContainment safeFallbackPolicy reversibility abortCondition'.split(),
      'ActionExecution':'executedAt expiresAt abortCondition safeFallbackPolicy reversibility failureContainment executionStatus'.split(),
      'Decision':['decisionTime'],
      'AssistantSession':[],
    }
    prop_by_iri={p['iri']:p for k in ['dataProperties','objectProperties'] for p in ext[k]}
    rows=[]
    for name,iri in classes.items():
        fields=props(name)
        for field,d in fields.items():
            row={'classIri':iri,'jsonPointer':'/'+field,'predicateIri':None,'nodeKind':None,'datatype':None,
                 'source':f'schema module $defs/{name}/properties/{field}','mappingVersion':'1.0.0','status':'unmapped',
                 'sequencePolicy':'preserve-in-source-json','nullPolicy':'omit-and-report','referencePolicy':None,'unit':None,'time':None}
            pred=None; nk='Literal'; dt=None
            if field=='type': pred=RDF+'type';nk='ClassReference'
            elif field=='iri': row.update(status='identity',nodeKind='Subject',referencePolicy='preserve-source-iri')
            elif field=='id': pred=A+'agentId' if name=='AgentInstance' else DCT+'identifier';dt=X+'string'
            elif field=='label': pred=RDFS+'label';dt=X+'string'
            elif field=='runId': pred=A+'withinRun';nk='IRI';row['referencePolicy']='resolve-Run-in-input-or-registry'
            elif field in relation.get(name,{}): pred=A+relation[name][field];nk='IRI';row['referencePolicy']='exact-iri-or-unambiguous-alias; no-invented-type'
            elif field in scalar.get(name,[]):
                pred=A+field;dt=prop_by_iri.get(pred,{}).get('range')
                if dt and dt.startswith('xsd:'): dt=X+dt[4:]
            elif name=='Run' and field in ['modelId','modelRevision']:
                pred=A+('runModelId' if field=='modelId' else 'runModelRevision');dt=X+'string'
            elif name=='ActionExecution' and field=='result': pred=A+'executionResult';dt=X+'string'
            elif field=='validTime': pred=A+'validDuring';nk='TimeInterval';row['time']='numeric native clock; no dateTime conversion without epoch'
            elif field=='provenance': pred=A+'hasProvenanceRecord';nk='ProvenanceRecord'
            elif name=='ActionExecution' and field=='effect': pred=A+'hasEffect';nk='EffectRecord'
            if pred:
                row.update(predicateIri=pred,nodeKind=nk,datatype=dt,status='mapped',sequencePolicy='unordered-set; duplicates-reported' if d.get('type')=='array' else 'scalar')
            rows.append(row)
    # Schema coverage does not itself imply a mapping.  Actual legacy record
    # fields not represented by a same-named Schema are explicitly registered
    # as unmapped, retaining their original JSON for later domain decisions.
    reg=read('identity-registry.json'); seen={(r['classIri'],r['jsonPointer']) for r in rows}
    from convert_sources import records_from_package
    for rec,meta in records_from_package(ROOT,include_classes=False):
        iri=classes.get(meta['type'])
        if not iri: continue
        for field in rec:
            key=(iri,'/'+field)
            if key in seen: continue
            seen.add(key)
            rows.append({'classIri':iri,'jsonPointer':'/'+field,'predicateIri':RDF+'type' if field=='type' else DCT+'identifier' if field=='id' else None,
                         'nodeKind':'ClassReference' if field=='type' else 'Literal' if field=='id' else None,'datatype':X+'string' if field=='id' else None,
                         'source':meta['file'],'mappingVersion':'1.0.0','status':'mapped' if field in ['id','type'] else 'unmapped','sequencePolicy':'preserve-in-source-json','nullPolicy':'omit-and-report','referencePolicy':None,'unit':None,'time':None})
    mapping={'mappingVersion':'1.0.0','package':'coast-raid-k','namespaces':{'base':B,'agent':A},'classIris':classes,'mappings':rows,
      'policy':'Only explicit mapped rows emit triples. Unmapped original values are retained in JSON and the conversion report. Identity fields identify subjects; they are not predicates.',
      'structuredFields':{'validTime':'TimeInterval node: intervalFrom/intervalTo/timeClock/timeUnit; null remains absent with a report','provenance':'InformationEntity node: dct:source, sourceSha256, hashStatus; additional fields remain loss-reported','effect':'Effect information record node; payload retained as rdf:JSON on rdf:value'}}
    write('semantic-mapping.json',mapping)
    schema={'$schema':'https://json-schema.org/draft/2020-12/schema','title':'Explicit JSON to RDF field mapping','type':'object','required':['mappingVersion','package','classIris','mappings'],
      'properties':{'mappingVersion':{'type':'string'},'package':{'const':'coast-raid-k'},'classIris':{'type':'object','additionalProperties':{'type':'string','format':'uri'}},'mappings':{'type':'array','items':{'type':'object','required':['classIri','jsonPointer','predicateIri','nodeKind','status','source','mappingVersion','sequencePolicy','nullPolicy'],'properties':{'classIri':{'type':'string','format':'uri'},'jsonPointer':{'type':'string','pattern':'^/'},'predicateIri':{'type':['string','null'],'format':'uri'},'nodeKind':{'type':['string','null']},'status':{'enum':['mapped','unmapped','identity']},'source':{'type':'string'},'mappingVersion':{'type':'string'},'sequencePolicy':{'type':'string'},'nullPolicy':{'type':'string'}},'allOf':[{'if':{'properties':{'status':{'const':'mapped'}}},'then':{'properties':{'predicateIri':{'type':'string','format':'uri'},'nodeKind':{'type':'string'}}}}]}}}}
    write('schema.semantic-mapping.json',schema)
    print(f'registered {len(rows)} fields; mapped={sum(r["status"]=="mapped" for r in rows)}, identity={sum(r["status"]=="identity" for r in rows)}, unmapped={sum(r["status"]=="unmapped" for r in rows)}')

if __name__=='__main__':
    generate_mapping(*update_vocabulary())
