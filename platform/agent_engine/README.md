# Agent Runtime Engine

Phase 2 implements a profile-driven fixed-plan runtime backed by PostgreSQL
`ontology_agent_admin`. It persists profile snapshots, checkpoints, step attempts,
tool calls, loops, approvals, final output and artifacts. Events, tool calls,
snapshots and artifacts have PostgreSQL mutation-rejection triggers.

Use the bootstrap to register all eight UAV tool adapters:

```python
import asyncio
import sys
sys.path.insert(0, "platform")
from agent_engine.bootstrap import build_engine
from agent_tools.fixtures import X200_CAPABILITY_INPUT

engine = build_engine()
run_id = engine.create_run("uav-capability-analyst", X200_CAPABILITY_INPUT)
result = asyncio.run(engine.execute(run_id))
```

For existing ontology records, freeze a read-only extraction before creating a Run:

```python
from agent_tools.ontology import capability_input

input_data = capability_input(
    "equipment-ff2d751e27858b020dd2",
    {"enduranceHours": 20, "payloadKg": 200},
)
run_id = engine.create_run("uav-capability-analyst", input_data)
result = asyncio.run(engine.execute(run_id))
```

The extractor reads `ontology/coast-raid-k/coast-raid-k-intelligence.sqlite`.
Provisional numeric comparisons retain evidence identifiers, units, source
versions, hashes, locators, verification status, conditions and missing dates.
An import date never becomes an observation date. Maximum speed never fills a
cruise-speed field. Missing telemetry remains unknown. Review and evidence gaps
prevent `completed`; the current real-data POC reaches `requires-human-review`.

```powershell
python platform/run_agent_engine_poc.py
python platform/run_agent_engine_poc.py --ontology-entity equipment-ff2d751e27858b020dd2
python -m unittest -v tests.test_agent_engine_runtime
python platform/verify_agent_engine_bootstrap.py
```

`build_app()` exposes execution, controls, approvals, steps, trace and artifacts
under `/api/agent-runs`. Execute/resume are synchronous HTTP requests that await
execution; there is no background job service in this phase. Pause/cancel stop
after the in-flight group. The deadline cancels cooperative async adapters.

Only `uav-capability-analyst` has executable steps. The design and reviewer Agents
are registered definitions for subsequent implementation. There are no runtime
LLM calls, MCP transport, arbitrary dependency DAG, mutating domain adapters,
distributed scheduler or production authentication in this implementation.
Profile completion text is evaluated through the fixed capability checks and
evidence rules; it is not an arbitrary expression language.

See `docs/agent-engine-runbook.md` for database initialization and
`docs/agent-engine-phase2-verification.md` for the acceptance record.
