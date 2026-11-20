/**
 * Recommendation providers are advisory only. Their output must be validated
 * and evaluated by Gate before an Authorization can be issued.
 */

function clone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

export class RecommendationProvider {
  async recommend() {
    throw new Error("recommendation provider is abstract");
  }
}

export class DeterministicRecommendationProvider extends RecommendationProvider {
  constructor({ providerId = "deterministic-recommendation", modelId = "rules-only", modelRevision = "v1" } = {}) {
    super();
    this.providerId = providerId;
    this.modelId = modelId;
    this.modelRevision = modelRevision;
  }

  async recommend({ runId, actorIri, targetIri, observations = [], beliefs = [], contextHash = "0000000000000000000000000000000000000000000000000000000000000000", tick = 0 } = {}) {
    const confidence = beliefs.length
      ? Math.max(...beliefs.map((x) => Number(x.confidence ?? x.probability ?? 0) || 0))
      : 0;
    const enoughEvidence = observations.length > 0 && confidence >= 0.75;
    const action = enoughEvidence ? "request-bounded-action" : "continue-tracking";
    const rationale = enoughEvidence
      ? "证据达到当前规则阈值，建议提交受范围限制的动作请求。"
      : "证据不足以支持动作请求，建议继续观测并补充身份/位置证据。";
    return {
      type: "DecisionDraft",
      id: `draft-${runId || "run"}-${tick}`,
      runId,
      proposedBy: actorIri,
      action,
      targetIri,
      rationale,
      alternatives: [
        { action: "continue-tracking", reason: "低风险、可逆" },
        { action: "request-more-evidence", reason: "提高证据覆盖" },
      ],
      uncertainty: enoughEvidence ? [] : ["identity-evidence-insufficient"],
      evidenceUris: [...observations, ...beliefs].map((x) => x.iri).filter(Boolean),
      provider: this.providerId,
      modelId: this.modelId,
      modelRevision: this.modelRevision,
      contextHash,
      recommendationStatus: "proposed",
      createdAtTick: tick,
      provenance: { source: "platform/recommendation-provider.js", hashStatus: "not-computed" },
    };
  }
}

export function validateRecommendation(draft) {
  const required = ["type", "runId", "proposedBy", "action", "rationale", "recommendationStatus", "provider"];
  const missing = required.filter((key) => draft?.[key] == null || draft[key] === "");
  if (missing.length) return { ok: false, errors: missing.map((key) => `missing ${key}`) };
  if (draft.type !== "DecisionDraft") return { ok: false, errors: ["type must be DecisionDraft"] };
  if (!["proposed", "superseded", "accepted", "rejected"].includes(draft.recommendationStatus)) {
    return { ok: false, errors: ["invalid recommendationStatus"] };
  }
  return { ok: true, errors: [] };
}

export function recommendationForLog(draft) {
  return clone({
    type: draft.type,
    id: draft.id,
    runId: draft.runId,
    proposedBy: draft.proposedBy,
    action: draft.recommendation?.action || draft.action,
    targetIri: draft.recommendation?.targetIri || draft.targetIri,
    evidenceUris: draft.evidenceUris || [],
    provider: draft.provider,
    modelId: draft.modelId,
    modelRevision: draft.modelRevision,
    contextHash: draft.contextHash,
  });
}
