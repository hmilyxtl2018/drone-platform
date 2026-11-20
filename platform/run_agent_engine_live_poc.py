"""Bounded paid Phase 3 connectivity probe. Saves real calls under one Run.

No mock/fallback is used. Defaults: one model attempt, one search, one scrape.
Read-only ontology snapshots remain candidates. Credentials load from the
existing encrypted store/environment and never enter output or snapshots.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from agent_engine.bootstrap import build_engine
from agent_engine.configuration import model_gateway
from agent_engine.domain import RunContext


ROOT = Path(__file__).resolve().parents[1]


async def probe(entity_id, *, database="ontology_agent_admin"):
    engine = build_engine(database=database)
    run_id = engine.create_run("uav-capability-analyst", {
        "executionMode": "research", "entityId": entity_id,
        "question": "Verify model/source connectivity and retain evidence as pending review.",
        "budgetLimits": {"model_calls": 1, "http_calls": 3, "search_calls": 1, "scrape_calls": 1,
                         "tool_calls": 4, "input_tokens": 30000, "output_tokens": 2048}})
    profile = engine.loader.load_for_run(run_id)
    ctx = RunContext(run_id, profile, engine.repo.get_run(run_id)["input_json"])
    report = {"runId": run_id, "checkedAt": datetime.now(timezone.utc).isoformat(),
              "probeScope": "3A-connectivity; not full phase3 acceptance", "passed": False}
    for status in ("queued", "planning", "running"):
        engine.repo.transition(run_id, status)

    async def tool(key, arguments):
        step_key = "probe-" + key.replace(".", "-")
        sid = engine.repo.step(run_id, step_key, "running", input_data=arguments)
        try:
            result = await engine.gateway.invoke(ctx, sid, key,
                                                  {"input": ctx.input, "state": {}, "arguments": arguments}, step_key)
            engine.repo.step(run_id, step_key, "succeeded", output=result)
            return result
        except Exception:
            engine.repo.step(run_id, step_key, "failed", error="probe-operation-failed")
            raise

    try:
        source = await tool("ontology.get_entity", {"entityId": entity_id})
        report["ontology"] = {"entityId": entity_id, "artifactId": source["artifactId"],
                              "revision": source["sourceSnapshot"]["revision"],
                              "sourceGaps": len(source["sourceGaps"]), "readOnly": True}
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["entityId", "truthVerified", "reportedEnduranceHours"],
                  "properties": {"entityId": {"const": entity_id}, "truthVerified": {"const": False},
                                 "reportedEnduranceHours": {"type": ["number", "null"]}}}
        gateway = model_gateway(engine.repo, profile)
        result = await gateway.invoke(ctx, [
            {"role": "system", "content": profile.system_prompt},
            {"role": "user", "content": "Extract the reported enduranceHours from this ontology record. "
             "Do not mark the claim verified. Return JSON. Record: " + json.dumps(source["uavData"], ensure_ascii=False)}],
            schema, stage="connectivity-probe")
        report["modelOutput"] = result
        query = (source["uavData"].get("name") or "MQ-1C Gray Eagle") + " endurance payload"
        search = await tool("web.search", {"query": query, "limit": 2})
        report["search"] = {"status": search["status"], "results": len(search["results"])}
        if not search["results"]:
            raise ValueError("probe-search-has-no-allowed-results")
        page = await tool("web.scrape", {"url": search["results"][0]["sourceUri"]})
        report["scrape"] = {key: page[key] for key in ("status", "sourceUri", "artifactId", "contentHash", "retrievedAt")}
        if page["status"] != "scraped":
            raise ValueError("probe-source-has-no-content")
        report["passed"] = True
        report["status"] = "requires-human-review"
        engine.repo.transition(run_id, "validating")
        engine.repo.transition(run_id, "requires-human-review", output={
            "completionStatus": "requires-human-review", "probe": report,
            "truthVerified": False, "unresolvedQuestions": ["Source candidates require citation extraction and human review."]})
    except Exception as exc:
        # Only known symbolic provider codes are persisted; no raw transport or
        # connection errors (which may include authorization/connection text).
        code = getattr(exc, "code", None)
        report.update(status="failed", errorCode=code if isinstance(code, str) else type(exc).__name__)
        engine.repo.transition(run_id, "failed", error=report["errorCode"])
    finally:
        engine.repo.save_state(ctx)
        report["budget"] = engine.repo.get_budget(run_id)
        report["modelCalls"] = [{key: row[key] for key in (
            "call_id", "requested_model", "actual_model", "status", "input_tokens", "output_tokens",
            "latency_ms", "http_status", "error_code")} for row in engine.repo.list_model_calls(run_id)]
        report["toolCalls"] = [{key: row[key] for key in ("tool_key", "status", "latency_ms")}
                               for row in engine.repo.list_tool_calls(run_id)]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity-id", default="equipment-ff2d751e27858b020dd2")
    parser.add_argument("--output", default="docs/verification/phase3/live-3a.json")
    args = parser.parse_args()
    report = asyncio.run(probe(args.entity_id))
    path = ROOT / args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
