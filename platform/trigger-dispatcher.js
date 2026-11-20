/**
 * Small application-level trigger dispatcher for the Agent runtime.
 *
 * Database writes happen before dispatch. This module only matches declarative
 * trigger definitions, applies idempotency/cooldown/concurrency guards, and
 * invokes a handler. It never issues Authorization or calls an effector.
 */

function valueAt(object, path) {
  return String(path).split(".").reduce((value, key) => value == null ? undefined : value[key], object);
}

function matchesWhen(event, when = {}) {
  return Object.entries(when).every(([path, expected]) => {
    const actual = valueAt(event, path);
    if (Array.isArray(expected)) return expected.includes(actual);
    if (expected && typeof expected === "object" && !Array.isArray(expected)) {
      if ("in" in expected) return expected.in.includes(actual);
      if ("neq" in expected) return actual !== expected.neq;
    }
    return actual === expected;
  });
}

function eventTypeOf(event) {
  return event?.eventType || event?.type || event?.kind;
}

export class TriggerDispatcher {
  constructor({ clock = () => Date.now(), onDispatch = async () => undefined } = {}) {
    this.clock = clock;
    this.onDispatch = onDispatch;
    this.definitions = new Map();
    this.seen = new Set();
    this.lastFiredAt = new Map();
    this.inFlight = new Map();
  }

  register(definition) {
    if (!definition?.id || !definition?.eventType) throw new Error("trigger requires id and eventType");
    const def = {
      mode: "auto",
      enabled: true,
      cooldownMs: 0,
      maxConcurrency: 1,
      requiresApproval: false,
      when: {},
      ...definition,
    };
    this.definitions.set(def.id, def);
    return def;
  }

  unregister(id) {
    return this.definitions.delete(id);
  }

  list() {
    return [...this.definitions.values()].map((x) => ({ ...x }));
  }

  _dedupeKey(def, event) {
    if (event?.dedupeKey) return `${def.id}:${event.dedupeKey}`;
    return `${def.id}:${event?.eventId || event?.iri || event?.id || JSON.stringify(event)}`;
  }

  _eligible(def, event, options) {
    if (!def.enabled) return { ok: false, reason: "disabled" };
    if (eventTypeOf(event) !== def.eventType) return { ok: false, reason: "event-type-mismatch" };
    if (!matchesWhen(event, def.when)) return { ok: false, reason: "condition-mismatch" };
    if (def.runId && event.runId !== def.runId) return { ok: false, reason: "run-mismatch" };
    if (def.visibility && event.visibility !== def.visibility) return { ok: false, reason: "visibility-mismatch" };
    const manual = options.mode === "manual";
    if (def.mode === "manual" && !manual) return { ok: false, reason: "manual-only" };
    const dedupeKey = this._dedupeKey(def, event);
    if (!options.force && this.seen.has(dedupeKey)) return { ok: false, reason: "duplicate", dedupeKey };
    const now = this.clock();
    const previous = this.lastFiredAt.get(def.id);
    if (!options.force && previous != null && now - previous < Number(def.cooldownMs || 0)) {
      return { ok: false, reason: "cooldown", dedupeKey };
    }
    const running = this.inFlight.get(def.id) || 0;
    if (running >= Math.max(1, Number(def.maxConcurrency) || 1)) {
      return { ok: false, reason: "max-concurrency", dedupeKey };
    }
    return { ok: true, dedupeKey };
  }

  async dispatch(event, { mode = "auto", force = false, context = {} } = {}) {
    const results = [];
    for (const def of this.definitions.values()) {
      const eligible = this._eligible(def, event, { mode, force });
      if (!eligible.ok) continue;
      const now = this.clock();
      this.seen.add(eligible.dedupeKey);
      this.lastFiredAt.set(def.id, now);
      this.inFlight.set(def.id, (this.inFlight.get(def.id) || 0) + 1);
      const fire = {
        triggerId: def.id,
        eventId: event?.eventId || event?.iri || event?.id,
        runId: event?.runId,
        mode,
        requiresApproval: Boolean(def.requiresApproval),
        targetAgent: def.targetAgent,
        firedAt: new Date(now).toISOString(),
      };
      try {
        const output = await this.onDispatch({ definition: def, event, fire, context });
        results.push({ ...fire, status: "fired", output });
      } catch (error) {
        results.push({ ...fire, status: "failed", error: String(error?.message || error) });
      } finally {
        this.inFlight.set(def.id, Math.max(0, (this.inFlight.get(def.id) || 1) - 1));
      }
    }
    return results;
  }
}

