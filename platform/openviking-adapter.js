/**
 * OpenViking boundary adapter.
 *
 * This module is intentionally transport-free: it turns a RuntimeLedger
 * snapshot into records that can be written with the OpenViking Python/Go/TS
 * SDK or MCP client. It does not perform retrieval, inference, or
 * authorization. The caller must authenticate to OpenViking and apply its
 * account/ACL policy before writing these records.
 */

export const CONTEXT_TYPES = Object.freeze({
  OPERATIONAL: "OperationalContext",
  AGENT_LOCAL: "AgentLocalContext",
  ASSISTANT_SESSION: "AssistantSessionContext",
});

const safe = (value) => encodeURIComponent(String(value));

export function contextPath({ contextType, accountId = "default", runId, agentIri, sessionId } = {}) {
  if (contextType === CONTEXT_TYPES.OPERATIONAL) return `viking://resources/coast-raid-k/runs/${safe(runId)}/`;
  if (contextType === CONTEXT_TYPES.AGENT_LOCAL) {
    if (!agentIri) throw new Error("AgentLocalContext requires agentIri");
    return `viking://users/${safe(accountId)}/agents/${safe(agentIri)}/runs/${safe(runId)}/`;
  }
  if (contextType === CONTEXT_TYPES.ASSISTANT_SESSION) {
    if (!sessionId) throw new Error("AssistantSessionContext requires sessionId");
    return `viking://users/${safe(accountId)}/sessions/${safe(sessionId)}/`;
  }
  throw new Error(`unsupported contextType: ${contextType}`);
}

function envelope({ iri, contextType, contextOwner, producer, runId, visibility, ontologyVersion = "1.0.0", validTime, payload, assistantActor }) {
  if (!iri || !contextOwner || !producer || !runId) throw new Error("context record is missing stable identity");
  return {
    iri,
    contextType,
    contextOwner,
    producer,
    ...(assistantActor ? { assistantActor } : {}),
    runId,
    visibility,
    ontologyVersion,
    validTime: validTime || { clock: "sim", from: 0, to: null },
    provenance: { source: "platform/agent-runtime.js", relation: "runtime-snapshot" },
    payload,
  };
}

/** Split a runtime snapshot into shared and per-Agent records. */
export function snapshotToContextRecords(snapshot, { accountId = "default", ontologyVersion = "1.0.0" } = {}) {
  if (!snapshot?.run?.id) throw new Error("runtime snapshot requires run.id");
  const runId = snapshot.run.id;
  const runIri = snapshot.run.iri;
  const records = [];
  records.push(envelope({
    iri: runIri,
    contextType: CONTEXT_TYPES.OPERATIONAL,
    contextOwner: runIri,
    producer: "https://local.ontology/coast-raid-k/agent/1.0.0#simulation-engine",
    runId,
    visibility: "shared",
    ontologyVersion,
    payload: { type: "RunSummary", run: snapshot.run, auditEvents: snapshot.auditEvents },
  }));

  for (const agent of snapshot.agents || []) {
    const owned = {
      agent,
      observations: (snapshot.observations || []).filter((x) => x.assertedBy === agent.iri),
      beliefs: (snapshot.beliefs || []).filter((x) => x.assertedBy === agent.iri),
      decisions: (snapshot.decisions || []).filter((x) => x.decidedBy === agent.iri),
      actionRequests: (snapshot.actionRequests || []).filter((x) => x.requestedBy === agent.iri),
      authorizations: (snapshot.authorizations || []).filter((x) => x.decidedBy === agent.iri),
      executions: (snapshot.executions || []).filter((x) => x.realizedBy === agent.iri),
    };
    records.push(envelope({
      iri: agent.iri,
      contextType: CONTEXT_TYPES.AGENT_LOCAL,
      contextOwner: agent.iri,
      producer: agent.iri,
      runId,
      visibility: "agent-private",
      ontologyVersion,
      payload: owned,
    }));
  }
  return records;
}

/** Create a session record without promoting assistant text to a belief. */
export function assistantSessionRecord({ sessionId, operatorIri, representedAgentIri, runId, query, retrievalTrace = [], accountId = "default", ontologyVersion = "1.0.0" }) {
  if (!operatorIri) throw new Error("assistant session requires operatorIri");
  const iri = `https://local.ontology/coast-raid-k/agent/1.0.0#assistant-session-${safe(sessionId)}`;
  return envelope({
    iri,
    contextType: CONTEXT_TYPES.ASSISTANT_SESSION,
    contextOwner: iri,
    producer: operatorIri,
    assistantActor: "https://local.ontology/coast-raid-k/agent/1.0.0#assistant",
    runId,
    visibility: "operator-private",
    ontologyVersion,
    payload: { type: "AssistantSession", sessionId, operatorIri, representedAgentIri, query, retrievalTrace },
  });
}

export function openVikingManifest(snapshot, options = {}) {
  const records = snapshotToContextRecords(snapshot, options);
  return {
    backend: "OpenViking",
    runId: snapshot.run.id,
    operationalUri: contextPath({ contextType: CONTEXT_TYPES.OPERATIONAL, runId: snapshot.run.id }),
    records,
    writeOrder: ["operational", "agent-local", "assistant-session"],
    retrieval: { scopeFirst: true, requireL2EvidenceForAuthorization: true },
  };
}
