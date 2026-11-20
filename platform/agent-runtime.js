/**
 * Runtime projection for the Agent ontology extension.
 *
 * The battle renderer can keep its compact mutable state (`p`, `belief`,
 * `task`, ...), while this ledger records the semantic objects that make the
 * state replayable: AgentInstance, Observation, BeliefAssertion, Decision,
 * ActionRequest, Authorization, ActionExecution and AuditEvent.
 */

export const AGENT_NS = "https://local.ontology/coast-raid-k/agent/1.0.0#";
export const RUN_NS = "https://local.ontology/coast-raid-k/run/";

// These are controlled values in the Agent extension.  They describe a
// composable responsibility projection; they are deliberately not Agent
// subclasses and do not grant authority by themselves.
export const AGENT_FUNCTIONS = Object.freeze(["World", "Self", "Adversary", "Options", "Gate", "Effectors", "Learn"]);
export const TIME_SCALES = Object.freeze(["Immediate", "Tactical", "Operational", "Strategic"]);
export const HUMAN_CONTROL_MODES = Object.freeze(["in-the-loop", "on-the-loop", "out-of-the-loop"]);
export const CONNECTIVITY_MODES = Object.freeze(["online", "degraded", "disconnected"]);
export const REVERSIBILITY_CLASSES = Object.freeze(["reversible", "recoverable", "irreversible"]);
export const FAILURE_BOUNDARIES = Object.freeze(["local-action", "team", "operational", "strategic"]);

function safePart(value) {
  return String(value).replace(/[^A-Za-z0-9._:-]+/g, "-");
}

export function agentIri(id) {
  return `${AGENT_NS}${safePart(id)}`;
}

export function runIri(runId) {
  return `${RUN_NS}${encodeURIComponent(runId)}`;
}

/** Simulation timestamps are ISO strings so they pass the interchange schema. */
export function simDateTime(tick = 0) {
  const epoch = Date.UTC(2026, 0, 1, 0, 0, 0);
  return new Date(epoch + Math.max(0, Number(tick) || 0) * 40).toISOString();
}

function clone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

export class RuntimeLedger {
  constructor({ runId = "run-runtime", modelId = "coast-raid-k-demo", modelRevision = "runtime-v1", maxEvents = 2048 } = {}) {
    this.runId = runId;
    this.modelId = modelId;
    this.modelRevision = modelRevision;
    this.maxEvents = maxEvents;
    this.counters = Object.create(null);
    this.agents = new Map();
    this.observations = [];
    this.beliefs = [];
    this.decisions = [];
    this.decisionDrafts = [];
    this.actionRequests = [];
    this.approvalRequests = [];
    this.humanDecisions = [];
    this.authorizations = [];
    this.executions = [];
    this.auditEvents = [];
  }

  reset(runId = this.runId) {
    this.runId = runId;
    this.counters = Object.create(null);
    // Agent definitions are run-scoped and may include service agents that
    // have no physical marker. Keep them across a renderer reset; physical
    // instances with the same IDs are replaced by the next seed pass.
    for (const key of ["observations", "beliefs", "decisions", "decisionDrafts", "actionRequests", "approvalRequests", "humanDecisions", "authorizations", "executions", "auditEvents"]) {
      this[key] = [];
    }
  }

  _id(prefix) {
    this.counters[prefix] = (this.counters[prefix] || 0) + 1;
    return `${prefix}-${this.counters[prefix]}`;
  }

  _push(collection, value) {
    collection.push(value);
    if (collection.length > this.maxEvents) collection.splice(0, collection.length - this.maxEvents);
    return value;
  }

  _provenance(source = "platform/runtime") {
    // Runtime records are emitted before a content-addressed export exists.
    // Do not claim a fake digest; the exporter can attach the real SHA-256.
    return { source, hashStatus: "not-computed", generatedBy: agentIri("runtime-ledger") };
  }

  _ref(value) {
    if (typeof value === "string" && value.startsWith("http")) return value;
    const key = String(value);
    return this.agents.get(key)?.iri || agentIri(`${this.runId}-${key}`);
  }

  _eventIri(id) {
    return agentIri(`${this.runId}-${id}`);
  }

  registerAgent({
    id, side, layer, agentType, implementedOn, organization, roles = [], capabilities = [], authorityScope = [],
    functionDomain = [], timeScale = [], humanControlMode = "on-the-loop", connectivityMode = "online",
    failureContainment = "local-action", safeFallbackPolicy = "deny-new-authorization", reversibility = "reversible",
    authorizationTtlSeconds = 8, decisionMode = "hybrid", autonomyLevel = "delegated",
  }) {
    const iri = agentIri(`${this.runId}-${id}`);
    const instance = {
      type: "AgentInstance",
      id,
      iri,
      agentType: agentType || agentIri(`agent-type-${side || "unknown"}-${layer || "generic"}`),
      implementedOn: implementedOn || `${AGENT_NS}platform-${side || "unknown"}`,
      organization: organization || agentIri(side === "blue" ? "org-blue" : "org-red"),
      roles: [...roles],
      capabilities: [...capabilities],
      runId: this.runId,
      modelId: this.modelId,
      modelRevision: this.modelRevision,
      decisionMode,
      autonomyLevel,
      functionDomain: [...new Set(functionDomain)].filter((x) => AGENT_FUNCTIONS.includes(x)),
      timeScale: [...new Set(timeScale)].filter((x) => TIME_SCALES.includes(x)),
      humanControlMode: HUMAN_CONTROL_MODES.includes(humanControlMode) ? humanControlMode : "on-the-loop",
      connectivityMode: CONNECTIVITY_MODES.includes(connectivityMode) ? connectivityMode : "online",
      failureContainment: FAILURE_BOUNDARIES.includes(failureContainment) ? failureContainment : "local-action",
      safeFallbackPolicy,
      reversibility: REVERSIBILITY_CLASSES.includes(reversibility) ? reversibility : "reversible",
      authorizationTtlSeconds: Math.max(1, Number(authorizationTtlSeconds) || 8),
      lifecycleState: "initialized",
      authorityScope: [...authorityScope],
      validTime: { from: 0, to: null, clock: "sim", unit: "tick" },
      provenance: this._provenance("platform/battle.js#makeAgent"),
    };
    this.agents.set(id, instance);
    return instance;
  }

  activate(id, tick = 0) {
    const instance = this.agents.get(id);
    if (instance) {
      instance.lifecycleState = "active";
      instance.validTime.from = tick;
    }
    return instance;
  }

  observe({ assertedBy, observedEntity, observationKind, tick = 0, sourceIri, confidence = 0.5, location, payload = {}, frame = "coast-raid-k-local" }) {
    const id = this._id("obs");
    const observation = {
      type: "Observation",
      id,
      iri: this._eventIri(id),
      assertedBy: this._ref(assertedBy),
      observedEntity: this._ref(observedEntity),
      observationKind,
      observedAt: simDateTime(tick),
      sourceIri: sourceIri || agentIri("sensor-fusion"),
      confidence: Math.max(0, Math.min(1, Number(confidence) || 0)),
      frame,
      location: location ? [...location] : undefined,
      payload: clone(payload),
      runId: this.runId,
      provenance: this._provenance("platform/battle.js#step"),
    };
    return this._push(this.observations, observation);
  }

  believe({ assertedBy, aboutIri, proposition, predicate = "hasBeliefProbability", objectValue, probability, confidence, derivedFrom, tick = 0, derivedBy = "cloud-belief-revision" }) {
    const id = this._id("belief");
    const belief = {
      type: "BeliefAssertion",
      id,
      iri: this._eventIri(id),
      assertedBy: this._ref(assertedBy),
      aboutIri: this._ref(aboutIri),
      predicate,
      proposition,
      objectValue,
      derivedFrom: (derivedFrom || []).map((x) => (String(x).startsWith("http") ? x : agentIri(x))),
      derivedBy,
      confidence: Math.max(0, Math.min(1, Number(confidence ?? probability ?? 0) || 0)),
      probability: Math.max(0, Math.min(1, Number(probability ?? confidence ?? 0) || 0)),
      validTime: { from: tick, to: null, clock: "sim", unit: "tick" },
      runId: this.runId,
      provenance: this._provenance("platform/knowledge.js#belief-revision"),
    };
    return this._push(this.beliefs, belief);
  }

  decide({ agent, task, action, target, reason, tick = 0, evidence = [], policy = "coast-raid-k-default" }) {
    const id = this._id("decision");
    const decision = {
      type: "Decision",
      id,
      iri: this._eventIri(id),
      decidedBy: this._ref(agent),
      taskIri: task ? agentIri(task) : undefined,
      selectedAction: action,
      targetIri: target ? this._ref(target) : undefined,
      rationale: reason,
      evidenceUris: evidence.map((x) => (String(x).startsWith("http") ? x : this._ref(x))),
      policy,
      decisionTime: simDateTime(tick),
      runId: this.runId,
      provenance: this._provenance("platform/battle.js#reactBlue"),
    };
    return this._push(this.decisions, decision);
  }

  /**
   * A recommendation is an advisory object. It is never an authorization and
   * cannot be consumed by an effector directly. Keeping it separate from
   * Decision makes the human checkpoint explicit and replayable.
   */
  recommend({ agent, action, target, rationale, alternatives = [], uncertainty = [], evidence = [], decision, tick = 0, provider = "deterministic-recommendation", modelId, modelRevision, contextHash }) {
    const id = this._id("draft");
    const recommendation = {
      action,
      targetIri: target ? this._ref(target) : undefined,
      rationale,
    };
    const draft = {
      type: "DecisionDraft",
      id,
      iri: this._eventIri(id),
      proposedBy: this._ref(agent),
      decisionIri: decision?.iri,
      recommendation,
      rationale,
      alternatives: clone(alternatives).map((item) => typeof item === "string" ? { action: item } : { action: item.action, targetIri: item.targetIri, parameters: item.parameters, rationale: item.rationale, risk: item.risk, expectedEffect: item.expectedEffect }),
      evidenceUris: evidence.map((x) => (String(x).startsWith("http") ? x : this._ref(x))),
      uncertainties: clone(uncertainty),
      status: "draft",
      provider,
      modelId,
      modelRevision,
      contextHash,
      createdAt: simDateTime(tick),
      runId: this.runId,
      provenance: this._provenance("platform/runtime#recommendation"),
    };
    return this._push(this.decisionDrafts, draft);
  }

  request({ agent, action, target, decision, tick = 0, parameters = {} }) {
    const id = this._id("request");
    const request = {
      type: "ActionRequest",
      id,
      iri: this._eventIri(id),
      requestedBy: this._ref(agent),
      action,
      targetIri: target ? this._ref(target) : undefined,
      decisionIri: decision?.iri,
      parameters: clone(parameters),
      requestedAt: simDateTime(tick),
      runId: this.runId,
      status: "pending",
      provenance: this._provenance("platform/battle.js#action-request"),
    };
    return this._push(this.actionRequests, request);
  }

  requestApproval({ request, recommendation, requestedBy, requiredHumanControlMode = "in-the-loop", risk = "unknown", expiresAtTick, tick = 0, reason }) {
    const id = this._id("review");
    const review = {
      type: "ApprovalRequest",
      id,
      iri: this._eventIri(id),
      actionRequestIri: request.iri,
      recommendationIri: recommendation?.iri,
      requestedBy: this._ref(requestedBy || request.requestedBy),
      requiredHumanControlMode: HUMAN_CONTROL_MODES.includes(requiredHumanControlMode) ? requiredHumanControlMode : "in-the-loop",
      risk,
      status: "pending",
      reviewStatus: "pending",
      reason,
      evidenceUris: [...(recommendation?.evidenceUris || [])],
      submittedAt: simDateTime(tick),
      requestedAt: simDateTime(tick),
      expiresAtTick: expiresAtTick == null ? tick + 120 : Number(expiresAtTick),
      expiresAt: simDateTime(expiresAtTick == null ? tick + 120 : Number(expiresAtTick)),
      runId: this.runId,
      provenance: this._provenance("platform/runtime#human-review"),
    };
    request.status = "pending-human";
    request.approvalRequestIri = review.iri;
    const stored = this._push(this.approvalRequests, review);
    this.audit("human-review-requested", request.iri, tick, { approvalRequestIri: review.iri });
    return stored;
  }

  recordHumanDecision({ review, operator, decision, reason, tick = 0, revisedRequestIri, sessionIri, evidence = [] }) {
    const allowed = ["approved", "rejected", "request-more-evidence", "deferred", "escalated"];
    if (!review || !allowed.includes(decision)) throw new Error(`unsupported human decision: ${decision}`);
    const id = this._id("human");
    const humanDecision = {
      type: "HumanDecision",
      id,
      iri: this._eventIri(id),
      approvalRequestIri: review.iri,
      actionRequestIri: review.actionRequestIri,
      decidedBy: this._ref(operator),
      decision,
      reason,
      revisedRequestIri,
      sessionIri,
      decidedAt: simDateTime(tick),
      evidenceUris: evidence.length ? evidence.map((x) => (String(x).startsWith("http") ? x : this._ref(x))) : [...(review.evidenceUris || [])],
      runId: this.runId,
      provenance: this._provenance("platform/runtime#human-decision"),
    };
    review.reviewStatus = decision;
    review.status = decision;
    review.decisionIri = humanDecision.iri;
    const request = this.actionRequests.find((x) => x.iri === review.actionRequestIri);
    if (request) {
      request.status = decision === "approved"
        ? "human-approved"
        : decision === "rejected"
          ? "denied"
          : decision === "request-more-evidence"
            ? "pending-evidence"
            : decision;
      request.humanDecisionIri = humanDecision.iri;
    }
    const stored = this._push(this.humanDecisions, humanDecision);
    this.audit("human-decision-recorded", request?.iri || review.actionRequestIri, tick, { decision: decision, humanDecisionIri: humanDecision.iri });
    return stored;
  }

  authorize({
    request, decidedBy, result, reasonCode, checks = {}, evidence = [], ruleVersion = "gate-v1", tick = 0,
    ttlSeconds, scope = [], abortConditions = ["authorization-invalid", "link-lost", "unsafe-state"], safeFallbackPolicy, reversibility = "irreversible",
    failureContainment = "local-action", requiredHumanControlMode = "in-the-loop", revocable = true,
    humanDecision, approvalRequest,
  }) {
    const id = this._id("auth");
    const gate = this.agents.get(String(decidedBy));
    const gateAllowed = gate && gate.functionDomain.includes("Gate");
    const decisionResult = gateAllowed && ["allow", "deny", "indeterminate"].includes(result) ? result : "deny";
    const finalReason = gateAllowed ? reasonCode : "gate-agent-required";
    const ttl = Math.max(1, Number(ttlSeconds || gate?.authorizationTtlSeconds || 8));
    const issuedAt = simDateTime(tick);
    const authorization = {
      type: "Authorization",
      id,
      iri: this._eventIri(id),
      requestIri: request.iri,
      decidedBy: this._ref(decidedBy),
      result: decisionResult,
      authorizationStatus: "active",
      reasonCode: finalReason,
      checks: { ...checks },
      decisionTime: issuedAt,
      issuedAt,
      expiresAt: simDateTime(tick + ttl),
      expiresAtTick: tick + ttl,
      revocable: Boolean(revocable),
      scope: clone(Array.isArray(scope) && scope.length ? scope : (request.targetIri ? [request.targetIri] : [])),
      abortConditions: [...abortConditions],
      safeFallbackPolicy: safeFallbackPolicy || gate?.safeFallbackPolicy || "deny-new-authorization",
      reversibility: REVERSIBILITY_CLASSES.includes(reversibility) ? reversibility : "irreversible",
      failureContainment: FAILURE_BOUNDARIES.includes(failureContainment) ? failureContainment : "local-action",
      requiredHumanControlMode: HUMAN_CONTROL_MODES.includes(requiredHumanControlMode) ? requiredHumanControlMode : "in-the-loop",
      ruleVersion,
      policyVersion: ruleVersion,
      evidenceUris: evidence.map((x) => (String(x).startsWith("http") ? x : this._ref(x))),
      approvalRequestIri: approvalRequest?.iri,
      humanDecisionIri: humanDecision?.iri,
      decidedByFunctionDomain: "Gate",
      runId: this.runId,
      provenance: this._provenance("platform/knowledge.js#authorize"),
    };
    request.status = decisionResult === "allow" ? "authorized" : decisionResult === "deny" ? "denied" : "indeterminate";
    request.authorizationIri = authorization.iri;
    const stored = this._push(this.authorizations, authorization);
    this.audit("authorization-issued", request.iri, tick, { authorizationIri: authorization.iri, result: decisionResult });
    return stored;
  }

  finalizeAuthorization({ request, approvalRequest, decidedBy, humanDecision, ...options }) {
    const linked = approvalRequest?.actionRequestIri === request?.iri
      && humanDecision?.approvalRequestIri === approvalRequest?.iri;
    if (!linked || humanDecision.decision !== "approved") {
      request.status = "pending-human";
      return null;
    }
    return this.authorize({
      request,
      approvalRequest,
      humanDecision,
      decidedBy,
      ...options,
    });
  }

  revoke(authorization, reason = "revoked", tick = 0) {
    if (!authorization || !authorization.revocable) return authorization;
    authorization.authorizationStatus = "revoked";
    authorization.revokedAt = simDateTime(tick);
    authorization.revocationReason = reason;
    const request = this.actionRequests.find((x) => x.iri === authorization.requestIri);
    if (request && request.status === "authorized") request.status = "revoked";
    this.audit("authorization-revoked", authorization.iri, tick, { reason });
    return authorization;
  }

  execute({ request, executor, result = "completed", effect = {}, tick = 0, abortTriggered = false }) {
    const id = this._id("exec");
    const authorization = this.authorizations.find((x) => x.iri === request?.authorizationIri);
    const effector = this.agents.get(String(executor));
    this.audit("execution-started", request?.iri || "unknown-request", tick, { executor: this._ref(executor), authorizationIri: authorization?.iri || null });
    if (authorization?.authorizationStatus === "active" && Number(tick) > Number(authorization.expiresAtTick)) {
      authorization.authorizationStatus = "expired";
    }
    const validAuthorization = authorization
      && authorization.result === "allow"
      && authorization.authorizationStatus === "active"
      && Number(tick) <= Number(authorization.expiresAtTick)
      && !abortTriggered;
    const validEffector = effector && effector.functionDomain.includes("Effectors");
    const executable = Boolean(validAuthorization && validEffector);
    const finalResult = executable ? result : "blocked-unsafe";
    const execution = {
      type: "ActionExecution",
      id,
      iri: this._eventIri(id),
      requestIri: request.iri,
      realizedBy: this._ref(executor),
      result: finalResult,
      executionStatus: executable ? "executed" : "aborted",
      authorizationIri: authorization?.iri,
      expiresAt: authorization?.expiresAt,
      abortCondition: (authorization?.abortConditions || ["authorization-invalid", "link-lost", "unsafe-state"]).join("; "),
      safeFallbackPolicy: authorization?.safeFallbackPolicy || effector?.safeFallbackPolicy || "deny-new-authorization",
      reversibility: authorization?.reversibility || effector?.reversibility || "irreversible",
      failureContainment: authorization?.failureContainment || effector?.failureContainment || "local-action",
      effect: executable ? clone(effect) : { safeFallbackApplied: true },
      executedAt: simDateTime(tick),
      runId: this.runId,
      provenance: this._provenance("platform/battle.js#action-execution"),
    };
    request.executionIri = execution.iri;
    request.status = executable ? "executed" : "aborted";
    const stored = this._push(this.executions, execution);
    if (executable) this.audit("effect-recorded", request.iri, tick, { executionIri: execution.iri, effect: clone(effect) });
    this.audit(executable ? "execution-completed" : "execution-aborted", request.iri, tick, { executionIri: execution.iri, status: execution.executionStatus });
    return stored;
  }

  audit(kind, subject, tick = 0, payload = {}) {
    const id = this._id("audit");
    return this._push(this.auditEvents, {
      type: "AuditEvent",
      id,
      iri: this._eventIri(id),
      kind,
      subjectIri: this._ref(subject),
      occurredAt: simDateTime(tick),
      runId: this.runId,
      payload: clone(payload),
      provenance: this._provenance("platform/runtime#audit"),
    });
  }

  snapshot() {
    return {
      run: { type: "Run", id: this.runId, iri: runIri(this.runId), modelId: this.modelId, modelRevision: this.modelRevision },
      agents: [...this.agents.values()].map(clone),
      observations: clone(this.observations),
      beliefs: clone(this.beliefs),
      decisions: clone(this.decisions),
      decisionDrafts: clone(this.decisionDrafts),
      actionRequests: clone(this.actionRequests),
      approvalRequests: clone(this.approvalRequests),
      humanDecisions: clone(this.humanDecisions),
      authorizations: clone(this.authorizations),
      executions: clone(this.executions),
      auditEvents: clone(this.auditEvents),
    };
  }
}
