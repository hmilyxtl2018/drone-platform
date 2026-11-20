/** Ontology cloud-shadow slice for the envelope demo. Li Deyi cloud: (Ex, En, He). */

function randn() {
  let u = 0;
  let v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

export function mu(x, cloud) {
  const en = Math.max(1e-6, cloud.En);
  const d = x - cloud.Ex;
  return Math.exp((-d * d) / (2 * en * en));
}

export function mergeCloud(a, b) {
  const wa = 1 / (a.En * a.En + 1e-9);
  const wb = 1 / (b.En * b.En + 1e-9);
  return {
    Ex: (a.Ex * wa + b.Ex * wb) / (wa + wb),
    En: Math.sqrt(1 / (wa + wb)),
    He: Math.min(a.He, b.He),
  };
}

export class CloudKB {
  constructor({ tau = 0.85, nMaxLo = 6 } = {}) {
    this.tau = tau;
    this.nMaxLo = nMaxLo;
    this.reset();
  }

  reset() {
    this.objects = {
      B_SAM: { type: "envelope", layer: "mid" },
      B_BMD: { type: "envelope", layer: "hi" },
      C_low: { type: "corridor", layer: "lo" },
      wave: { type: "wave", k: 0 },
      channel: { type: "channel", m: 4 },
    };
    this.clouds = {
      inSam: { Ex: 0.72, En: 0.16, He: 0.03 },
      nEnv: { Ex: 4, En: 0.7, He: 0.12 },
      threatSam: { Ex: 0.8, En: 0.14, He: 0.04 },
    };
    this.shadows = {
      team: { f: 0.12, mu: 0.05, n: 0 },
      local: { f: 0.12, mu: 0.05, n: 0 },
    };
    this.rules = { nOk: true, corOk: true, gateOk: true };
    this.log = [];
    this.beliefRevision = 0;
    this.observationRevision = 0;
    this.authorizationRevision = 0;
  }

  /**
   * Build a provenance-bearing local observation for the runtime Agent
   * ledger. CloudKB owns evidence fusion; the Agent owns the resulting belief
   * and the Gate Monitor owns authorization.
   */
  observe({ agentId, entityId, kind, tick = 0, confidence = 0.5, payload = {} }) {
    this.observationRevision += 1;
    return {
      id: `kb-obs-${this.observationRevision}`,
      assertedBy: agentId,
      observedEntity: entityId,
      observationKind: kind,
      observedAt: tick,
      confidence: Math.max(0, Math.min(1, confidence)),
      payload: { ...payload },
    };
  }

  /** Record the revision produced by a CloudKB fusion pass. */
  reviseBelief({ agentId, entityId, probability, derivedFrom = [], tick = 0, proposition = "threatIdentity=hostile" }) {
    this.beliefRevision += 1;
    const p = Math.max(0, Math.min(1, Number(probability) || 0));
    return {
      id: `kb-belief-${this.beliefRevision}`,
      assertedBy: agentId,
      aboutIri: entityId,
      proposition,
      probability: p,
      confidence: p,
      derivedFrom: [...derivedFrom],
      derivedBy: "CloudKB.cloud-shadow-fusion",
      validTime: { from: tick, to: null, clock: "sim", unit: "tick" },
    };
  }

  /**
   * Gate Monitor policy evaluation. Returning a structured result keeps
   * authorization separate from the boolean physics transition.
   */
  authorizeGate(agent, state, samHit, feature) {
    this.authorizationRevision += 1;
    const checks = {
      inEngagementZone: Boolean(samHit),
      channelAvailable: Number(state.mSam) > 0,
      communicationsAvailable: !state.jammed,
      identityConfidence: Number(agent.belief) >= this.tau,
    };
    let reasonCode = "allow-engagement";
    let result = "allow";
    if (!checks.inEngagementZone) {
      result = "deny";
      reasonCode = "outside-engagement-zone";
    } else if (!checks.channelAvailable) {
      result = "deny";
      reasonCode = "channels-exhausted";
    } else if (!checks.communicationsAvailable) {
      result = "indeterminate";
      reasonCode = "communications-denied";
    } else if (!checks.identityConfidence) {
      result = "deny";
      reasonCode = "belief-below-threshold";
    }
    this.log.push({ kind: "authorization", id: `kb-auth-${this.authorizationRevision}`, agentId: agent.id, result, reasonCode, feature, checks });
    if (this.log.length > 256) this.log.shift();
    return { result, reasonCode, checks, feature, revision: this.authorizationRevision };
  }

  feature(p, sam) {
    const d = Math.hypot(p[0] - sam.c[0], p[1] - sam.c[1], p[2] - sam.c[2]) / sam.r;
    return Math.max(-0.25, Math.min(1, 1 - d));
  }

  sampleN(room) {
    const c = this.clouds.nEnv;
    const enp = c.En + randn() * c.He;
    const drop = Math.round(c.Ex + randn() * Math.abs(enp));
    return Math.max(3, Math.min(room, this.nMaxLo, drop));
  }

  mayGate(agent, state, samHit, f) {
    if (agent.gated) return false;
    return this.authorizeGate(agent, state, samHit, f).result === "allow";
  }

  tick(state, sam, living, inCor) {
    const live = living(state.lo);
    const feats = live.map((a) => this.feature(a.p, sam));
    const mean = (xs) => (xs.length ? xs.reduce((s, v) => s + v, 0) / xs.length : 0);
    const fLoc = mean(feats);
    this.shadows.local.f = fLoc;
    this.shadows.local.mu = mu(fLoc, this.clouds.inSam);
    this.shadows.local.n = live.length;

    if (!state.jammed) {
      this.shadows.team.f += (fLoc - this.shadows.team.f) * 0.2;
      this.shadows.team.mu = mu(this.shadows.team.f, this.clouds.inSam);
      this.shadows.team.n = live.length;
      this.clouds.inSam.He += (0.03 - this.clouds.inSam.He) * 0.08;
      this.clouds.inSam.En += (0.16 - this.clouds.inSam.En) * 0.05;
    } else {
      this.clouds.inSam.He += (0.14 - this.clouds.inSam.He) * 0.12;
      this.clouds.inSam.En += (0.28 - this.clouds.inSam.En) * 0.08;
    }

    const edge = feats.filter((f) => f > 0.05 && f < 0.35).length;
    if (edge) this.clouds.inSam.En = Math.min(0.32, this.clouds.inSam.En + 0.002 * edge);

    this.objects.wave.k = state.k;
    this.objects.channel.m = state.mSam;
    this.rules.nOk = live.length <= this.nMaxLo;
    this.rules.corOk = live.every((a) => inCor(a.p));
    this.rules.gateOk = live.every((a) => !a.gated || a.belief >= this.tau);
  }

  evolve(kind) {
    if (kind === "wave") this.clouds.nEnv.Ex = Math.min(this.nMaxLo, this.clouds.nEnv.Ex + 0.05);
    if (kind === "gate") this.clouds.inSam.Ex = Math.min(0.85, this.clouds.inSam.Ex + 0.01);
    if (kind === "attrition") this.clouds.nEnv.Ex = Math.max(3, this.clouds.nEnv.Ex - 0.15);
  }

  readout() {
    const c = this.clouds.inSam;
    const t = this.shadows.team;
    const l = this.shadows.local;
    const r = this.rules;
    return {
      cloud: `Ex ${c.Ex.toFixed(2)}  En ${c.En.toFixed(2)}  He ${c.He.toFixed(2)}`,
      team: `f ${t.f.toFixed(2)}  μ ${t.mu.toFixed(2)}`,
      local: `f ${l.f.toFixed(2)}  μ ${l.mu.toFixed(2)}`,
      rules: `${r.nOk ? "N" : "N!"} · ${r.corOk ? "廊" : "廊!"} · ${r.gateOk ? "门" : "门!"}`,
    };
  }
}
