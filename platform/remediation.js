import {validationLabel} from './kb-profiles.js';
const esc=x=>String(x??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const byId=id=>document.getElementById(id);
const table=(heads,rows)=>`<div class="semantic-scroll"><table class="semantic-table"><thead><tr>${heads.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(x=>`<td>${esc(x)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
const entries=[
 ['F01','类定义与壳体实例页签混用','implemented','Class 使用定义、类结构、字段与关系、约束、个体、来源六页；Individual 按实际记录选择适用页签。全局能力与无报告的合规结论已移除。','Observation'],
 ['F02','继承关系方向错误','implemented','类结构按“当前类 subClassOf 上位类”展示；多个父类分别列出，导航支持多继承与循环保护。','Sensor'],
 ['F03','JSON Schema 与 OWL 约束混写','implemented','JSON 文档约束与本体公理分别展示；required 表示必须有键，minItems / maxItems 单独表示数组数量。三层校验独立报告。','AgentInstance'],
 ['F04','同短名谓词被覆盖','implemented','属性索引使用完整 IRI；基础 memberOf 与 Agent memberOf 保留各自的定义、domain 和 range。','AgentInstance'],
 ['F05','按字段名猜测 RDF 类型','partial','JSON 结构类型来自 Schema；RDF 谓词、节点种类来自按类映射契约。无正式契约的字段显示“未映射”，剩余映射见当前转换报告。','Observation'],
 ['F06','个体页缺实际取值','implemented','属性与关系页读取当前主体原始记录，展示全部顶层值、嵌套内容及适用字段缺项；三条 AgentInstance 不再只显示类字段说明。','AgentInstance'],
 ['F07','广域属性误当本类字段','implemented','区分本类域声明与祖先域候选；domain 不用来自动填充实例事实或推定必填项。字段适用性与 JSON 契约分开。','Goal'],
 ['F08','无型谱引用却展示全型库','implemented','每个个体只显示自身记录与明确关联；平台型谱留在 PlatformType 目录中，不扩散到无引用的 Scene 对象。','PlatformType'],
 ['F09','包级内容缺少归属','implemented','CRS、包级单位、来源与标准采用范围明确标为包级参考；独立关联记录保留源路径和方向，不当作对象自带字段。','Region'],
 ['F10','几何字段混作能力、单位混用','implemented','半径、位置等归入几何视图并保留原字段名；长度单位与速度、角度、时间约定区分，未登记内容不填统一单位。','DetectionDisk'],
 ['F11','FunctionBind 出现默认签发者','implemented','职能绑定按其实际字段和输入输出展示；删除从全局 permitWriter 推定每个职能签发者的行为。','FunctionBind'],
 ['F12','CloudDef 被误称 Quality','implemented','直接类型保持 CloudDef，上位类为 InformationEntity；数值特征不能把信息实体变成 Quality。','CloudDef'],
 ['F13','搜索与字符串包含造成串类','implemented','按完整 IRI 查询类、实际记录与公理；搜索只过滤导航，类的个体清单不随搜索结果改变。歧义短名不静默选中。','Sensor'],
 ['F14','导航缺类与个体入口','implemented','两种导航视图覆盖 98 类和 96 个体，补齐主题分类、关系个体 Membership 与 Package 直接入口。','Package'],
 ['F15','公理登记与形式导出范围不一致','partial','结构化编译器输出 ontology.ttl 和 Shape；逐条记录载体、编译状态与限制。未支持或只适合运行时解释的规则保留明确状态，不能冒充已执行 OWL。','Entity'],
 ['F16','JSON → RDF → SHACL 缺统一契约','partial','建立按类字段映射、身份索引、转换报告及逐主体校验。现有数据的未映射值、外部目标缺失和模拟时间单位缺项仍需补齐。','AgentInstance'],
 ['F17','定义缺失与概念边界不清','partial','补齐 98 类定义并清理占位内容；保留 IRI 与继承，显式记录定义审议状态、边界及决策。概念样例用于审议，领域确认仍待完成。','Event'],
 ['F18','标准名称被当作符合性证据','implemented','标准登记采用范围、版本和证据；无逐字段证据时只标参考或待核验。seeAlso 不当作等价类，项目图示不宣称标准认证。','Identity'],
 ['F19','阶段与图符展示重复','implemented','阶段按当前模板顺序展示，保留五个步骤及同名步骤的序号；SIDC 与项目示意图集中于一个页签。','ShellTemplate'],
 ['F20','包围盒冒充精确几何','implemented','页面保留解析几何；数据库将包围盒和岸线采样改用明确的近似字段，附平面、坐标轴、采样及工程 CRS 说明。没有地理锚定时不编造 EPSG。','Envelope'],
 ['F21','false / 0 / 缺值与默认谱系混淆','implemented','显式区分 false、0、null、空字符串、空数组和缺字段。原始谱系完整保留；缺少谱系时不制造默认记录。','CloudDef'],
];
function renderIssues(){
  const filter=byId('filter').value;
  byId('issues').innerHTML=entries.filter(x=>filter==='all'||x[2]===filter).map(([id,title,status,change,target])=>`<details class="issue"><summary><strong>${id}</strong><span>${esc(title)}</span><b class="status ${status}">${status==='partial'?'有待完成项':'已实施'}</b></summary><p>${esc(change)}</p><div class="issue-links"><a href="./kb.html#Class/${encodeURIComponent(target)}">查看 ${esc(target)}</a></div></details>`).join('');
}
renderIssues();byId('filter').addEventListener('change',renderIssues);
try {
  const response=await fetch('/api/kb/boot'); const boot=await response.json();
  if(!response.ok||boot.ok===false)throw new Error(boot.error||`HTTP ${response.status}`);
  const files=boot.files||{},report=files['semantics/semantic-validation-report.json'];
  const inventory=boot.inventory?.entries||[],classes=inventory.filter(e=>e.type==='Class').length;
  const mappings=files['semantic-mapping.json']?.mappings||[];
  byId('metrics').innerHTML=[[classes,'类'],[inventory.length-classes,'个体'],[entries.length,'整改问题族'],[mappings.length,'字段映射契约']].map(([n,label])=>`<div class="metric"><strong>${n}</strong><span>${label}</span></div>`).join('');
  if(!report){byId('release-state').textContent='当前发布未附语义验证报告，不能据此判定校验通过。';byId('validation').textContent='未执行 / 未附报告';byId('residuals').textContent='请加载成套语义候选发布以查看数据残项。';}
  else {
    byId('release-state').textContent=`${validationLabel(report.status)}。页面与报告来自同一静态发布；当前数据尚未完成全量语义验收。`;
    byId('validation').innerHTML=table(['检查层','当前结果','命中目标','未通过条数'],Object.entries(report.validation||{}).map(([name,v])=>[name,validationLabel(v.status),v.targetCount??'未记录',v.violations?.length??0]));
    const conversion=report.conversion||{},counts=conversion.counts||{};
    const residuals=[
      ['转换覆盖',`已映射 ${counts.mapped??'未记录'} 项；未映射 ${counts.unmapped??'未记录'} 项；引用告警 ${counts.referenceWarnings??'未记录'} 条；有损或无法完整表达 ${counts.losses??'未记录'} 项；按约定省略 ${counts.omitted??'未记录'} 项。具体来源与原因见转换报告。`],
      ['SHACL',`${report.shacl?.violations?.length||0} 条未通过；${(report.shacl?.coverage||[]).filter(x=>!x.targetCount).length} 个 Shape 未覆盖。零目标不作为通过。`],
      ['领域复核','类定义、区别特征、边界和说明样例已按项目来源登记；领域审议尚未完成。'],
      ['形式化范围','OWL RL 检查不能替代完整 OWL 2 DL 一致性判定；解释性规则与未映射字段不计作已验证。'],
    ];
    byId('residuals').innerHTML=residuals.map(([label,value])=>`<p class="residual"><strong>${esc(label)}：</strong>${esc(value)}</p>`).join('');
  }
  const base='/ontology/coast-raid-k/';
  const evidence=[['identity-registry.json','身份登记'],['semantic-mapping.json','字段映射'],['semantic-decisions.json','语义决策'],['class-definition-review.json','类定义审议'],['standards-evidence.json','标准采用范围'],['semantics/semantic-validation-report.json','完整验证报告'],['semantics/conversion-report.json','转换与残项'],['semantics/semantic-export-report.json','规则编译报告']];
  byId('evidence').innerHTML=`<div class="evidence-links">${evidence.filter(([file])=>files[file]).map(([file,label])=>`<a href="${base+file}" target="_blank" rel="noopener">${esc(label)}</a>`).join('')}</div>`;
  byId('version').textContent=`包 ${boot.package?.version||'未记录'} · ${boot.store?.kind||'未记录'} · 发布 ${boot.releaseVersion||'未提供版本哈希'} · 执行时间 ${report?.executedAt||'未执行'}`;
}catch(error){byId('release-state').textContent=`读取失败：${error.message}`;}
