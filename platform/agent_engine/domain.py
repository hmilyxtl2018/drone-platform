from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

class RunStatus(str, Enum):
    CREATED='created'; QUEUED='queued'; PLANNING='planning'; RUNNING='running'
    WAITING_TOOL='waiting-tool'; WAITING_HUMAN='waiting-human'; RETRYING='retrying'
    LOOPING='looping'; VALIDATING='validating'; COMPLETED='completed'
    COMPLETED_WITH_GAPS='completed-with-gaps'; REQUIRES_HUMAN='requires-human-review'
    BLOCKED='blocked'; FAILED='failed'; CANCELLED='cancelled'

@dataclass(frozen=True)
class ToolBinding:
    key: str
    version: str
    enabled: bool = True
    approval_required: bool = False
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 60
    retry_limit: int = 2
    risk_level: str = 'medium'
    access_mode: str = 'read_only'
    protocol: str = 'python'
    endpoint: str | None = None
    config: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class Step:
    step_id: str
    key: str
    name: str
    sequence: int
    parallel_group: str | None
    required: bool
    retry_limit: int
    tools: tuple[str, ...] = ()
    input_contract: dict[str, Any] = field(default_factory=dict)
    output_contract: dict[str, Any] = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()
    description: str = ''

@dataclass(frozen=True)
class Profile:
    agent_key: str
    version: str
    display_name: str
    steps: tuple[Step, ...]
    tools: dict[str, ToolBinding]
    convergence: dict[str, Any]
    output_contract: dict[str, Any]
    evidence_policy: dict[str, Any]
    resource_policy: dict[str, Any]
    approval_policy: dict[str, Any]
    loop_rules: tuple[dict[str, Any], ...] = ()
    system_prompt: str = ''
    model_policy: dict[str, Any] = field(default_factory=dict)
    scope_policy: dict[str, Any] = field(default_factory=dict)
    source_policy: dict[str, Any] = field(default_factory=dict)
    validation_rules: tuple[dict[str, Any], ...] = ()
    observability_policy: dict[str, Any] = field(default_factory=dict)
    capability_graph: dict[str, Any] = field(default_factory=dict)
    # Harness metadata is optional for legacy databases and in-memory tests;
    # published Profiles on the P0 schema always carry this contract.
    agent_layer: str = 'application'
    runtime_kind: str = 'llm_loop'
    harness_contract: dict[str, Any] = field(default_factory=dict)
    execution_baseline: dict[str, Any] = field(default_factory=dict)
    reasoning_policy: dict[str, Any] = field(default_factory=dict)
    goal_contract: dict[str, Any] = field(default_factory=dict)
    skill_manifest: dict[str, Any] = field(default_factory=dict)

@dataclass
class RunContext:
    run_id: str
    profile: Profile
    input: dict[str, Any]
    state: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    tool_calls: int = 0
    model_calls: int = 0
    iteration: int = 0
    started_at: float | None = None
    tool_semaphore: Any = None
    # Set on a shallow per-tool call context so adapters can attribute durable
    # HTTP/model reservations to the executing Harness node without racing
    # parallel steps that share the mutable Run state.
    current_step_key: str | None = None
    current_step_run_id: str | None = None
    # Context identity is immutable for one logical invocation.  These fields
    # are optional so legacy in-memory tests and pre-055 Runs keep working.
    root_run_id: str | None = None
    role_key: str | None = None
    node_key: str | None = None
    context_id: str | None = None
    context_manifest_hash: str | None = None

ToolHandler = Callable[[dict[str, Any], RunContext], Awaitable[dict[str, Any]]]
