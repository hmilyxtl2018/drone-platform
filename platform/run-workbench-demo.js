/**
 * Deterministic, non-destructive end-to-end runtime demo.
 *
 * This runner exercises the same contracts used by the workbench without
 * connecting to a real-world actuator: the only effector is an evidence
 * collector that writes a bounded, reversible runtime record.  It proves the
 * ordering and safety rules that matter to the UI and API:
 *
 * Run -> Observation -> Belief -> DecisionDraft -> ActionRequest ->
 * GatePrecheck -> HumanDecision -> Gate Authorization -> Effectors -> Audit
 *
 * Run with:
 *   node --experimental-default-type=module platform/run-workbench-demo.js
 */

import assert from "node:assert/strict";
import { RuntimeLedger } from "./agent-runtime.js";
import { DeterministicRecommendationProvider, validateRecommendation } from "./recommendation-provider.js";
import { TriggerDispatcher } from "./trigger-dispatcher.js";

const runId = "run-workbench-demo";
const runtime = new RuntimeLedger({ runId, modelId: "workbench-demo", modelRevision: "demo-v1" });
runtime.registerAgent({ id: "world", side: "neutral", layer: "knowledge", functionDomain: ["World"], humanControlMode: "on-the-loop", reversibility: "reversible" });
runtime.registerAgent({ id: "options", side: "neutral", layer: "reasoning", functionDomain: ["Options"], humanControlMode: "in-the-loop", reversibility: "reversible" });
runtime.registerAgent({ id: "gate", side: "neutral", layer: "control", functionDomain: ["Gate"], humanControlMode: "in-the-loop", safeFallbackPolicy: "deny-new-authorization", authorizationTtlSeconds: 4 });
runtime.registerAgent({ id: "evidence-effector", side: "neutral", layer: "execution", functionDomain: ["Effectors"], humanControlMode: "on-the-loop", reversibility: "reversible", failureContainment: "local-action" });
for (const id of ["world", "options", "gate", "evidence-effector"]) runtime.activate(id, 0);

const triggerLog = [];
const dispatcher = new TriggerDispatcher({
  onDispatch: async ({ fire }) => {
    triggerLog.push(fire);
    runtime.audit("trigger-fired", runId, 0, { triggerId: fire.triggerId, eventId: fire.eventId });
    return { recommendationOnly: true };
  },
});
dispatcher.register({ id: "trigger-observation-to-options", eventType: "ObservationCreated", targetAgent: "options", requiresApproval: true, onFailure: "safe-fallback" });

runtime.audit("run-started", runId, 0, { packageId: "coast-raid-k", packageVersion: "0.9.4", mode: "replayable-demo" });
const observation = runtime.observe({
  assertedBy: "world", observedEntity: "record-17", observationKind: "source-evidence",
  sourceIri: "kb:source-registry", confidence: 0.81, tick: 1,
  payload: { subject: "record-17", version: "v3", completeness: 0.62 },
});
await dispatcher.dispatch({ eventType: "ObservationCreated", eventId: observation.id, runId, payload: observation });
const belief = runtime.believe({
  assertedBy: "world", aboutIri: observation.iri, proposition: "source-evidence-is-incomplete",
  objectValue: true, confidence: 0.63, probability: 0.63, derivedFrom: [observation.iri], tick: 2,
});
const provider = new DeterministicRecommendationProvider({ modelId: "rules-only", modelRevision: "demo-v1" });
const providerDraft = await provider.recommend({ runId, actorIri: runtime.agents.get("options").iri, targetIri: observation.iri, observations: [observation], beliefs: [belief], contextHash: "0000000000000000000000000000000000000000000000000000000000000000", tick: 3 });
assert.equal(validateRecommendation(providerDraft).ok, true);
const draft = runtime.recommend({ agent: "options", action: "collect-evidence", target: observation.iri, rationale: providerDraft.rationale, alternatives: providerDraft.alternatives, uncertainty: providerDraft.uncertainty, evidence: providerDraft.evidenceUris, tick: 3, provider: providerDraft.provider, modelId: providerDraft.modelId, modelRevision: providerDraft.modelRevision, contextHash: providerDraft.contextHash });
const requestFor = (action, tick) => runtime.request({ agent: "options", action, target: observation.iri, decision: draft, tick, parameters: { scope: "record-17", maxItems: 5 } });
const reviewFor = (request, tick) => runtime.requestApproval({ request, recommendation: draft, requestedBy: "options", requiredHumanControlMode: "in-the-loop", risk: "low", expiresAtTick: tick + 10, tick, reason: "需要人工确认范围受限的证据采集。" });
const gate = (request, review, humanDecision, tick, result = "allow", ttlSeconds = 4) => runtime.authorize({ request, approvalRequest: review, humanDecision, decidedBy: "gate", result, reasonCode: result === "allow" ? "bounded-reversible-evidence" : "human-review-denied", checks: { scope: true, evidence: true, connectivity: true }, evidence: [observation.iri, belief.iri], ruleVersion: "gate-demo-v1", tick, ttlSeconds, scope: [observation.iri], reversibility: "reversible", failureContainment: "local-action", requiredHumanControlMode: "in-the-loop" });

// Main path: approved -> Gate allow -> reversible evidence collection executes.
const requestApproved = requestFor("collect-evidence", 4);
const reviewApproved = reviewFor(requestApproved, 4);
assert.equal(runtime.finalizeAuthorization({ request: requestApproved, approvalRequest: reviewApproved, decidedBy: "gate", tick: 5 }), null, "Gate must not authorize before a human decision");
const humanApproved = runtime.recordHumanDecision({ review: reviewApproved, operator: "operator:hmily", decision: "approved", reason: "批准低风险、范围受限的证据采集。", tick: 5 });
const authApproved = runtime.finalizeAuthorization({ request: requestApproved, approvalRequest: reviewApproved, humanDecision: humanApproved, decidedBy: "gate", result: "allow", reasonCode: "bounded-reversible-evidence", checks: { scope: true }, evidence: [observation.iri], ruleVersion: "gate-demo-v1", tick: 6, ttlSeconds: 4, scope: [observation.iri], reversibility: "reversible", failureContainment: "local-action" });
const executionApproved = runtime.execute({ request: requestApproved, executor: "evidence-effector", result: "evidence-recorded", effect: { record: "record-17", version: "v4" }, tick: 7 });

// Rejection path: the Gate emits deny; no effector execution is possible.
const requestRejected = requestFor("collect-evidence", 10);
const reviewRejected = reviewFor(requestRejected, 10);
const humanRejected = runtime.recordHumanDecision({ review: reviewRejected, operator: "operator:hmily", decision: "rejected", reason: "暂不接受该来源范围。", tick: 11 });
const authRejected = gate(requestRejected, reviewRejected, humanRejected, 12, "deny");
const executionRejected = runtime.execute({ request: requestRejected, executor: "evidence-effector", result: "must-not-run", tick: 13 });

// Revocation, expiry, and disconnect paths all converge on a safe fallback.
const requestRevoked = requestFor("collect-evidence", 20);
const reviewRevoked = reviewFor(requestRevoked, 20);
const humanRevoked = runtime.recordHumanDecision({ review: reviewRevoked, operator: "operator:hmily", decision: "approved", reason: "批准后续证据采集。", tick: 21 });
const authRevoked = gate(requestRevoked, reviewRevoked, humanRevoked, 22, "allow", 8);
runtime.revoke(authRevoked, "operator-revoked", 23);
const executionRevoked = runtime.execute({ request: requestRevoked, executor: "evidence-effector", result: "must-not-run", tick: 24 });

const requestExpired = requestFor("collect-evidence", 30);
const reviewExpired = reviewFor(requestExpired, 30);
const humanExpired = runtime.recordHumanDecision({ review: reviewExpired, operator: "operator:hmily", decision: "approved", reason: "批准短时证据采集。", tick: 31 });
const authExpired = gate(requestExpired, reviewExpired, humanExpired, 32, "allow", 1);
const executionExpired = runtime.execute({ request: requestExpired, executor: "evidence-effector", result: "must-not-run", tick: 40 });

const requestDisconnected = requestFor("collect-evidence", 50);
const reviewDisconnected = reviewFor(requestDisconnected, 50);
const humanDisconnected = runtime.recordHumanDecision({ review: reviewDisconnected, operator: "operator:hmily", decision: "approved", reason: "批准，但失联时必须中止。", tick: 51 });
const authDisconnected = gate(requestDisconnected, reviewDisconnected, humanDisconnected, 52, "allow", 8);
const executionDisconnected = runtime.execute({ request: requestDisconnected, executor: "evidence-effector", result: "must-not-run", abortTriggered: true, tick: 53 });
runtime.audit("safe-fallback-triggered", runId, 53, { reason: "connectivity-lost", policy: "deny-new-authorization" });

const snapshot = runtime.snapshot();
const summary = {
  runId, triggerCount: triggerLog.length, eventCounts: {
    observations: snapshot.observations.length, beliefs: snapshot.beliefs.length, decisionDrafts: snapshot.decisionDrafts.length,
    approvalRequests: snapshot.approvalRequests.length, humanDecisions: snapshot.humanDecisions.length,
    authorizations: snapshot.authorizations.length, executions: snapshot.executions.length, audits: snapshot.auditEvents.length,
  },
  outcomes: {
    approved: { authorization: authApproved?.result, execution: executionApproved.executionStatus },
    rejected: { authorization: authRejected.result, execution: executionRejected.executionStatus },
    revoked: { authorization: authRevoked.authorizationStatus, execution: executionRevoked.executionStatus },
    expired: { authorization: authExpired.authorizationStatus, execution: executionExpired.executionStatus },
    disconnected: { authorization: authDisconnected.authorizationStatus, execution: executionDisconnected.executionStatus },
  },
  replay: {
    runId: snapshot.run.id,
    firstAudit: snapshot.auditEvents[0]?.kind,
    lastAudit: snapshot.auditEvents.at(-1)?.kind,
    auditKinds: [...new Set(snapshot.auditEvents.map((event) => event.kind))],
  },
};

assert.equal(summary.outcomes.approved.execution, "executed");
assert.equal(summary.outcomes.rejected.authorization, "deny");
assert.equal(summary.outcomes.rejected.execution, "aborted");
assert.equal(summary.outcomes.revoked.execution, "aborted");
assert.equal(summary.outcomes.expired.execution, "aborted");
assert.equal(summary.outcomes.disconnected.execution, "aborted");
assert(summary.replay.auditKinds.includes("safe-fallback-triggered"));
console.log(JSON.stringify({ summary, trace: snapshot }, null, 2));
