"""Install explicit Phase 3 local configuration without embedding secrets.

This is an operator command, never called automatically by application startup.
Existing run snapshots and the UAV ontology remain untouched.
"""
from __future__ import annotations

import json
from copy import deepcopy

from psycopg2.extras import Json

from agent_engine.postgres import connection_factory
from agent_engine.plan_contracts import SOURCE_ARGUMENT_SCHEMAS
from agent_tools.ontology import DEFAULT_SOURCE, METRICS
from agent_tools.ontology_runtime import ENVELOPE_SCHEMA, SOURCE_SCHEMAS
from agent_tools.web import WEB_SCHEMAS
from agent_engine.role_artifacts import ROLE_DELIVERABLES


BUDGET = {"model_calls": 24, "input_tokens": 1200000, "output_tokens": 49152,
          "tool_calls": 64, "http_calls": 38, "search_calls": 6, "scrape_calls": 8}
SOURCE_POLICY = {"allowedDomains": ["ga-asi.com", "army.mil", "gao.gov", "congress.gov"],
                 "includeSubdomains": True, "maxSources": 6, "maxPages": 8,
                 "maxPayloadBytes": 2097152, "maxMarkdownChars": 150000}
PROMPTS = {
    "uav-capability-analyst": "Analyze reported UAV capabilities against the user's requirements. "
        "Preserve entity revision, aircraft variant, units, conditions and provenance. "
        "Search only material gaps. Every substantive claim needs an exact citation. "
        "Public specifications remain candidate claims; missing telemetry is unknown.",
    "uav-design-engineer": "Produce a conceptual UAV design brief, constraints, engineering data requests "
        "and a verification plan from supplied requirements and cited candidates. "
        "Never claim detailed design, manufacturing or flight validation without corresponding data.",
    "uav-evidence-reviewer": "Independently inspect UAV candidate claims for exact quotes, entity and variant "
        "attribution, units, conditions, source independence, conflicts and unsupported inference. "
        "Produce a review package and blocking issues. Your review is not human approval or publication."}


def configure(database="ontology_agent_admin"):
    if database != "ontology_agent_admin" and not database.startswith("agent_engine_verify_"):
        raise ValueError("invalid admin database")
    path = DEFAULT_SOURCE.resolve(strict=True)
    with connection_factory(database=database)() as con:
        with con.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended('agent-admin-profile-config',0))")
            providers = [("deepseek", "model", "https://api.deepseek.com", "env:DEEPSEEK_API_KEY",
                          {"timeoutSeconds": 60, "retryLimit": 1, "thinking": False}),
                         ("firecrawl", "web", "https://api.firecrawl.dev/v2", "env:FIRECRAWL_API_KEY", {})]
            for key, kind, endpoint, reference, config in providers:
                cursor.execute("INSERT INTO provider_registry(provider_key,provider_type,base_url,credential_ref,"
                               "enabled,config_json) VALUES(%s,%s,%s,%s,true,%s) ON CONFLICT(provider_key) DO NOTHING",
                               (key, kind, endpoint, reference, Json(config)))
            cursor.execute("INSERT INTO data_source_registry(source_key,connector,root_uri,enabled,read_only) "
                           "VALUES('uav-ontology','sqlite',%s,true,true) ON CONFLICT(source_key) DO NOTHING",
                           (path.as_uri(),))
            for key, output_schema in {**SOURCE_SCHEMAS, **WEB_SCHEMAS}.items():
                version = '3.0.1' if key == 'ontology.query_evidence' else '3.0.0'
                schema = deepcopy(ENVELOPE_SCHEMA)
                schema["properties"]["arguments"] = SOURCE_ARGUMENT_SCHEMAS[key]
                cursor.execute("INSERT INTO tool_registry(tool_key,tool_name,category,description,protocol,"
                               "input_schema,output_schema,tags_json,access_mode,risk_level,status,current_version) "
                               "VALUES(%s,%s,%s,%s,'python',%s,%s,'[]','read_only','low','active',%s) "
                               "ON CONFLICT(tool_key) DO NOTHING",
                               (key, key, "source", "Phase 3 bounded read-only source tool", Json(schema), Json(output_schema), version))
                cursor.execute("INSERT INTO tool_version(tool_key,version,input_schema,output_schema,changelog,status) "
                               "VALUES(%s,%s,%s,%s,'Phase 3 source contract','active') "
                               "ON CONFLICT(tool_key,version) DO NOTHING", (key, version, Json(schema), Json(output_schema)))
                if key == 'ontology.query_evidence':
                    cursor.execute("UPDATE tool_registry SET current_version=%s,input_schema=%s,output_schema=%s "
                                   "WHERE tool_key=%s AND current_version='3.0.0'", (version, Json(schema), Json(output_schema), key))
            cursor.execute("SELECT agent_key FROM agent_definition WHERE agent_key IN "
                           "('uav-capability-analyst','uav-design-engineer','uav-evidence-reviewer')")
            agents = [r["agent_key"] for r in cursor.fetchall()]
            for agent in agents:
                cursor.execute("UPDATE agent_definition SET system_prompt=%s WHERE agent_key=%s "
                               "AND (system_prompt IS NULL OR system_prompt='')", (PROMPTS[agent], agent))
                cursor.execute("SELECT config_json FROM agent_model_policy WHERE agent_key=%s", (agent,))
                row = cursor.fetchone()
                config = deepcopy((row or {}).get("config_json") or {})
                if not isinstance(config, dict):
                    config = json.loads(config)
                config.update({"researchModeAvailable": True, "ontologySourceKey": "uav-ontology",
                               "webProviderKey": "firecrawl", "sourcePolicy": SOURCE_POLICY,
                               "budgetLimits": BUDGET, "retryLimit": 1})
                config['researchSteps'] = [
                    {'key': 'read-frozen-source', 'executor': 'ontology.get_entity'},
                    {'key': 'plan-and-acquire' if agent == 'uav-capability-analyst' else 'inspect-upstream',
                     'executor': 'constrained-planner' if agent == 'uav-capability-analyst' else 'hash-bound-handoff'},
                    {'key': 'generate-deliverables', 'executor': 'model-and-deterministic-contract'},
                    {'key': 'validate-deliverables', 'requiredTypes': list(ROLE_DELIVERABLES[agent])}]
                config['researchOutputContract'] = {'required': ['summary','findings','evidence','assumptions',
                    'unresolvedQuestions','confidence','completionStatus','deliverables'],
                    'requiredDeliverables': list(ROLE_DELIVERABLES[agent]), 'selfApprovalAllowed': False}
                cursor.execute("UPDATE agent_model_policy SET model_provider='deepseek',model_name='deepseek-v4-pro',"
                               "max_output_tokens=2048,structured_output_required=1,config_json=%s WHERE agent_key=%s",
                               (Json(config), agent))
                for key in {**SOURCE_SCHEMAS, **WEB_SCHEMAS}:
                    version = '3.0.1' if key == 'ontology.query_evidence' else '3.0.0'
                    cursor.execute("INSERT INTO agent_tool_binding(agent_key,tool_key,tool_version,enabled,"
                                   "approval_required,config_json) VALUES(%s,%s,%s,1,0,%s) "
                                   "ON CONFLICT(agent_key,tool_key) DO NOTHING",
                                   (agent, key, version, Json({"retryLimit": 0, "timeoutSeconds": 60,
                                                      "argumentSchema": SOURCE_ARGUMENT_SCHEMAS[key]})))
                    if key == 'ontology.query_evidence':
                        cursor.execute("UPDATE agent_tool_binding SET tool_version=%s,config_json="
                                       "jsonb_set(config_json,'{argumentSchema}',%s) WHERE agent_key=%s "
                                       "AND tool_key=%s AND tool_version='3.0.0'",
                                       (version, Json(SOURCE_ARGUMENT_SCHEMAS[key]), agent, key))
    return {"database": database, "agentsConfigured": agents, "providers": [p[0] for p in providers],
            "sourceReadOnly": True, "newSourceTools": list({**SOURCE_SCHEMAS, **WEB_SCHEMAS}),
            "liveCallsMade": 0}


if __name__ == "__main__":
    print(json.dumps(configure(), ensure_ascii=False, indent=2))
