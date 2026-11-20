-- coast-raid-k 物理库：核心文档投影 + Agent 上下文文档索引。
-- 字典里的 Body / Rule / Class 等是对象类（行的 type），不要为每个字段建表。
-- PostgreSQL 14+（JSONB）。包版本来自 catalog.json，不是 dictionary.version。

CREATE TABLE crk_package (
  id            text NOT NULL,
  version       text NOT NULL,
  title         text,
  kind          text NOT NULL,
  envelope      jsonb NOT NULL,
  unit          jsonb NOT NULL,
  frame         jsonb NOT NULL,
  excludes      jsonb NOT NULL,
  parts         jsonb NOT NULL,
  PRIMARY KEY (id, version)
);

COMMENT ON TABLE crk_package IS '一包一个版本一行。主键 = catalog.id + catalog.version';

CREATE TABLE crk_part (
  package_id    text NOT NULL,
  version       text NOT NULL,
  file          text NOT NULL,
  sha256        text NOT NULL,
  doc           jsonb NOT NULL,
  PRIMARY KEY (package_id, version, file),
  FOREIGN KEY (package_id, version)
    REFERENCES crk_package (id, version)
    ON DELETE CASCADE
);

COMMENT ON TABLE crk_part IS '分册正文。theater.json / rules.json 等每个文件一行，全文进 doc';
COMMENT ON COLUMN crk_part.doc IS '该 JSON 文件的对象。几何、τ、N_max 都住在这里，不拆列';

CREATE TABLE crk_individual (
  package_id    text NOT NULL,
  version       text NOT NULL,
  type          text NOT NULL,
  id            text NOT NULL,
  nav           text NOT NULL,
  file          text NOT NULL,
  label         text,
  iri           text NOT NULL,
  PRIMARY KEY (package_id, version, type, id),
  UNIQUE (package_id, version, iri),
  FOREIGN KEY (package_id, version)
    REFERENCES crk_package (id, version)
    ON DELETE CASCADE
);

COMMENT ON TABLE crk_individual IS '命名个体索引 = inventory.json。一行一个可指称者，不存几何细节';
COMMENT ON COLUMN crk_individual.type IS '对象类：Body / Site / Rule / Class / CloudDef …';
COMMENT ON COLUMN crk_individual.id IS '稳定 ID：B_SAM、gate、inSam。复合键用 inventory 已有拼法，如 C_low∈SEA';
COMMENT ON COLUMN crk_individual.nav IS '七导航中文名：领域本体 / 战场 / 规则 / 算法 / 交战 / Agent / 知识';
COMMENT ON COLUMN crk_individual.iri IS 'Canonical IRI from identity-registry.json; source IRIs are preserved and historical base#id values are aliases only';

CREATE INDEX crk_individual_nav_idx
  ON crk_individual (package_id, version, nav);
CREATE INDEX crk_individual_type_idx
  ON crk_individual (package_id, version, type);
CREATE INDEX crk_individual_file_idx
  ON crk_individual (package_id, version, file);

CREATE TABLE crk_line (
  package_id    text NOT NULL,
  version       text NOT NULL,
  id            text NOT NULL,
  pred          text NOT NULL,
  from_id       text NOT NULL,
  from_type     text,
  to_id         text,
  to_type       text,
  nav           text NOT NULL,
  file          text NOT NULL,
  doc           jsonb,
  PRIMARY KEY (package_id, version, id),
  FOREIGN KEY (package_id, version)
    REFERENCES crk_package (id, version)
    ON DELETE CASCADE
);

COMMENT ON TABLE crk_line IS '关系线 = Relator / ASSOCIATION。两点一条谓词。G_t 本体在个体表，辐条在本表';
COMMENT ON COLUMN crk_line.id IS '稳定拼法：spoke:G_t:C2-SAM_TEL、owns:SAM_TEL-B_SAM、member:C_low∈SEA';
COMMENT ON COLUMN crk_line.pred IS 'owns|covers|feeds|commands|notSameAs|memberOf|boundTo|gives|subClassOf|disjointWith|link|deniedBy|bindsPlatformType|reads|writes';
COMMENT ON COLUMN crk_line.to_id IS '可空。例：owns 仅有波束、mesh 只有 among';
COMMENT ON COLUMN crk_line.doc IS 'km、role、mode、beam、rule 等线属性，不升列';

CREATE INDEX crk_line_pred_idx
  ON crk_line (package_id, version, pred);
CREATE INDEX crk_line_from_idx
  ON crk_line (package_id, version, from_id);
CREATE INDEX crk_line_to_idx
  ON crk_line (package_id, version, to_id);

CREATE TABLE crk_code (
  package_id    text NOT NULL,
  version       text NOT NULL,
  id            text NOT NULL,
  label         text,
  vals          jsonb NOT NULL,
  PRIMARY KEY (package_id, version, id),
  FOREIGN KEY (package_id, version)
    REFERENCES crk_package (id, version)
    ON DELETE CASCADE
);

COMMENT ON TABLE crk_code IS '字典值域 = dictionary.codes。vals 是字符串数组';

CREATE TABLE crk_source (
  package_id    text NOT NULL,
  version       text NOT NULL,
  id            text NOT NULL,
  used_as       text,
  iri           text,
  license       text,
  what          text,
  PRIMARY KEY (package_id, version, id),
  FOREIGN KEY (package_id, version)
    REFERENCES crk_package (id, version)
    ON DELETE CASCADE
);

COMMENT ON TABLE crk_source IS '开源出处 = sources.json。不是 40ms 快照';

CREATE TABLE crk_align (
  package_id    text NOT NULL,
  version       text NOT NULL,
  class_id      text NOT NULL,
  rel           text NOT NULL,
  iri           text NOT NULL,
  PRIMARY KEY (package_id, version, class_id, rel, iri),
  FOREIGN KEY (package_id, version)
    REFERENCES crk_package (id, version)
    ON DELETE CASCADE
);

COMMENT ON TABLE crk_align IS 'owl:equivalentClass / rdfs:seeAlso。IRI 来自 BFO/CCO/GeoSPARQL';

CREATE TABLE crk_facet (
  package_id    text NOT NULL,
  version       text NOT NULL,
  nav           text NOT NULL,
  tab           text NOT NULL,
  type          text NOT NULL,
  id            text NOT NULL,
  key           text NOT NULL,
  value         text,
  PRIMARY KEY (package_id, version, nav, tab, type, id, key),
  FOREIGN KEY (package_id, version)
    REFERENCES crk_package (id, version)
    ON DELETE CASCADE
);

COMMENT ON TABLE crk_facet IS '七导航 × 页签 × 个体 的键值。工作室页签的查询面';

CREATE INDEX crk_facet_nav_idx
  ON crk_facet (package_id, version, nav, tab);
CREATE INDEX crk_align_class_idx
  ON crk_align (package_id, version, class_id);

-- Agent extension documents (AgentInstance, Observation, BeliefAssertion,
-- Authorization).  The original JSON remains the source of record; this
-- table is a searchable projection keyed by package/version/type/id.
CREATE TABLE crk_agent_doc (
  package_id text NOT NULL,
  version text NOT NULL,
  type text NOT NULL,
  id text NOT NULL,
  iri text,
  run_id text,
  file text NOT NULL,
  sha256 text NOT NULL,
  doc jsonb NOT NULL,
  PRIMARY KEY (package_id, version, type, id),
  FOREIGN KEY (package_id, version)
    REFERENCES crk_package (id, version)
    ON DELETE CASCADE
);

CREATE INDEX crk_agent_doc_run_idx
  ON crk_agent_doc (package_id, version, run_id);

CREATE VIEW v_nav AS
  SELECT nav, type, id, label, file FROM crk_individual;

CREATE VIEW v_agent AS
  SELECT type, id, iri, run_id, file, sha256, doc
  FROM crk_agent_doc;

-- JSON 仍是 SoR。不要为 Body.r / Rule.tau 建列库。
-- 取某个体的正文：用 file 回到分册 JSON，再按 type 在数组里找 id。
-- 例：B_SAM → crk_part.file = theater.json → doc->'bodies' 里 id=B_SAM。
-- 取 SAM_TEL 拥有谁：SELECT * FROM crk_line WHERE pred='owns' AND from_id='SAM_TEL'。

-- 不要建的表：
-- Track / Identity / Pair / Slot / Permit / Inject / Event / Wave / Channel
-- Shadow / Candidate / GState / Quality
-- Body.r、Rule.tau、CloudDef.Ex 各一张列库
