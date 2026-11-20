"""Run the fixed UAV capability-analysis Agent Engine POC against PostgreSQL."""
from __future__ import annotations

import asyncio
import argparse
import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from agent_engine.bootstrap import build_engine
from agent_tools.fixtures import X200_CAPABILITY_INPUT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ontology-entity', help='read a real entity current revision instead of the X200 fixture')
    parser.add_argument('--source', help='read-only ontology SQLite path')
    parser.add_argument('--output', help='save the complete verification JSON')
    args = parser.parse_args()
    if args.ontology_entity:
        from agent_tools.ontology import capability_input
        input_data = capability_input(args.ontology_entity, {'enduranceHours': 20, 'payloadKg': 200}, args.source)
    else:
        input_data = X200_CAPABILITY_INPUT
    engine = build_engine()
    run_id = engine.create_run("uav-capability-analyst", input_data)
    result = asyncio.run(engine.execute(run_id, input_data=input_data))
    run = engine.repo.get_run(run_id)
    report = {
        "runId": run_id,
        "status": result["status"],
        "stepCount": len(engine.repo.list_steps(run_id)),
        "traceEventCount": len(engine.repo.list_events(run_id)),
        "artifactCount": len(engine.repo.list_artifacts(run_id)),
        "toolCallCount": len(engine.repo.list_tool_calls(run_id)),
        "outputPersisted": bool(run.get("output_json")),
        "output": result.get("output"),
    }
    if args.output:
        from pathlib import Path
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    # ASCII escapes also work in Windows consoles configured as cp1252.
    print(json.dumps(report, ensure_ascii=True, indent=2, default=str))
    acceptable = {'completed-with-gaps', 'requires-human-review'} if args.ontology_entity else {'completed', 'completed-with-gaps'}
    return 0 if result["status"] in acceptable and report['outputPersisted'] and report['artifactCount'] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
