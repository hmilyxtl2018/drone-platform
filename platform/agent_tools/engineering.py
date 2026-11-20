"""Versioned, deterministic requirement and concept-budget calculations.

Pure functions only: no database, provider, registry mutation or certification.
Numbers are evaluated as rational decimal inputs, so conversions and boundary
comparisons do not depend on binary floating point rounding. JSON output uses
finite numbers; saved input snapshots and formula versions support recomputation.
User-supplied provenance is traceability material, never trusted review authority.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math


CONTRACT_VERSION = "engineering.v1"
FORMULA_VERSION = "1.0.0"
SATISFIED, NOT_SATISFIED, UNKNOWN = "satisfied", "not-satisfied", "unknown"


class EngineeringInputError(ValueError):
    """A malformed or physically invalid input, distinct from missing material."""


# Rational factors to canonical units. Mass is kg, time s, energy Wh, power W,
# distance m, speed m/s and dimensionless fractions 1. No affine temperature
# conversions, density assumptions or cross-dimensional inference are supported.
_UNITS = {
    "kg": ("mass", Fraction(1), "kg"),
    "g": ("mass", Fraction(1, 1000), "kg"),
    "lb": ("mass", Fraction("0.45359237"), "kg"),
    "s": ("time", Fraction(1), "s"),
    "min": ("time", Fraction(60), "s"),
    "h": ("time", Fraction(3600), "s"),
    "W": ("power", Fraction(1), "W"),
    "kW": ("power", Fraction(1000), "W"),
    "Wh": ("energy", Fraction(1), "Wh"),
    "kWh": ("energy", Fraction(1000), "Wh"),
    "J": ("energy", Fraction(1, 3600), "Wh"),
    "kJ": ("energy", Fraction(5, 18), "Wh"),
    "m": ("distance", Fraction(1), "m"),
    "km": ("distance", Fraction(1000), "m"),
    "ft": ("distance", Fraction("0.3048"), "m"),
    "nmi": ("distance", Fraction(1852), "m"),
    "m/s": ("speed", Fraction(1), "m/s"),
    "km/h": ("speed", Fraction(5, 18), "m/s"),
    "kn": ("speed", Fraction(463, 900), "m/s"),
    "1": ("ratio", Fraction(1), "1"),
    "%": ("ratio", Fraction(1, 100), "1"),
}


@dataclass(frozen=True)
class _Quantity:
    minimum: Fraction
    maximum: Fraction
    dimension: str
    unit: str


_FORMULAS = {
    "requirement-comparison": {
        "formulaId": "requirement-comparison", "version": FORMULA_VERSION,
        "expression": "all possible actual/threshold pairs must satisfy the operator; otherwise false only when none do",
        "assumptions": ["Intervals are closed bounds, not probability distributions.",
                        "Numerical comparison does not establish evidence validity or engineering certification."],
    },
    "mass-budget": {
        "formulaId": "mass-budget", "version": FORMULA_VERSION,
        "expression": "budgetedMassKg = sum(componentMassKg) * (1 + contingencyFraction); headroomKg = massLimitKg - budgetedMassKg",
        "assumptions": ["The supplied component list defines the scope; completeness is not independently verified.",
                        "A concept contingency is an allowance, not measured additional mass.",
                        "Bounds are propagated conservatively without statistical independence assumptions."],
    },
    "energy-endurance": {
        "formulaId": "energy-endurance", "version": FORMULA_VERSION,
        "expression": "usableEnergyWh = energyWh * usableFraction * (1 - reserveFraction); enduranceHours = usableEnergyWh / averagePowerW",
        "assumptions": ["Average power must cover the supplied mission and configuration, including relevant loads.",
                        "Energy and power must refer to the same electrical or mechanical system boundary.",
                        "No fuel-to-electric, propulsion-efficiency, weather, aging or aerodynamic model is inferred.",
                        "Usable and reserve fractions are supplied explicit concept assumptions.",
                        "This is an energy balance estimate, not a flight-test result or certified endurance."],
    },
}


def formula_catalog():
    """Return detached formula descriptions suitable for later versioned storage."""
    return deepcopy(list(_FORMULAS.values()))


def _formula(name, version):
    if version != FORMULA_VERSION:
        raise EngineeringInputError("unsupported-formula-version")
    return deepcopy(_FORMULAS[name])


def _number(value):
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)) or value < 0:
        raise EngineeringInputError("quantity-requires-finite-nonnegative-number")
    try:
        return Fraction(str(value))
    except (ValueError, OverflowError):
        raise EngineeringInputError("quantity-exceeds-numeric-limit") from None


def _output_number(value):
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise EngineeringInputError("calculation-exceeds-finite-output") from None
    if not math.isfinite(result):
        raise EngineeringInputError("calculation-exceeds-finite-output")
    return result


def _quantity(value, dimension=None):
    if not isinstance(value, dict):
        raise EngineeringInputError("quantity-must-be-an-object")
    unit = value.get("unit")
    if not isinstance(unit, str) or unit not in _UNITS:
        raise EngineeringInputError("unsupported-unit")
    actual_dimension, factor, canonical = _UNITS[unit]
    if dimension is not None and dimension != actual_dimension:
        raise EngineeringInputError("unit-dimension-mismatch")
    if "value" in value:
        if "min" in value or "max" in value:
            raise EngineeringInputError("quantity-cannot-mix-point-and-interval")
        minimum = maximum = _number(value["value"])
    else:
        if "min" not in value or "max" not in value:
            raise EngineeringInputError("quantity-requires-value-or-closed-interval")
        minimum, maximum = _number(value["min"]), _number(value["max"])
        if minimum > maximum:
            raise EngineeringInputError("quantity-interval-is-reversed")
    return _Quantity(minimum * factor, maximum * factor, actual_dimension, canonical)


def _render(quantity, unit=None):
    unit = unit or quantity.unit
    if not isinstance(unit, str) or unit not in _UNITS or _UNITS[unit][0] != quantity.dimension:
        raise EngineeringInputError("unit-dimension-mismatch")
    factor = _UNITS[unit][1]
    return {"min": _output_number(quantity.minimum / factor), "max": _output_number(quantity.maximum / factor),
            "unit": unit, "dimension": quantity.dimension, "exact": quantity.minimum == quantity.maximum}


def normalize_quantity(value):
    """Normalize a point or a closed interval without losing uncertainty."""
    return _render(_quantity(value))


def convert_quantity(value, unit):
    return _render(_quantity(value), unit)


def _compare(actual, operator, target):
    if actual.dimension != target.dimension:
        raise EngineeringInputError("unit-dimension-mismatch")
    low, high, lower, upper = actual.minimum, actual.maximum, target.minimum, target.maximum
    if not isinstance(operator, str):
        raise EngineeringInputError("unsupported-comparison-operator")
    if operator == ">=":
        yes, no = low >= upper, high < lower
    elif operator == ">":
        yes, no = low > upper, high <= lower
    elif operator == "<=":
        yes, no = high <= lower, low > upper
    elif operator == "<":
        yes, no = high < lower, low >= upper
    elif operator == "==":
        yes, no = low == high == lower == upper, high < lower or low > upper
    elif operator == "between":
        yes, no = lower <= low <= high <= upper, high < lower or low > upper
    else:
        raise EngineeringInputError("unsupported-comparison-operator")
    return SATISFIED if yes else NOT_SATISFIED if no else UNKNOWN


def compare_quantities(actual, operator, target):
    """Pure interval mathematics, without entity/context/review assertions."""
    actual, target = _quantity(actual), _quantity(target)
    return {"result": _compare(actual, operator, target), "actual": _render(actual),
            "target": _render(target), "operator": operator, "formulaVersion": FORMULA_VERSION}


def _condition_value(value):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        if value.strip().casefold() in {"unknown", "unspecified", "not provided", "n/a", "未知", "未提供", "不明"}:
            return None
        if len(value) > 1000 or not value.strip():
            raise EngineeringInputError("invalid-condition-label")
        return value
    if isinstance(value, dict):
        return _quantity(value)
    raise EngineeringInputError("condition-requires-label-or-quantity")


def _conditions(value):
    if value is None or value == {}:
        return {}
    if not isinstance(value, dict) or len(value) > 50:
        raise EngineeringInputError("conditions-must-be-a-bounded-object")
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or len(key) > 120:
            raise EngineeringInputError("invalid-condition-name")
        result[key] = _condition_value(item)
    return result


def match_conditions(actual, required):
    """Require the same declared context, not a model-inferred similarity.

    Extra keys are also significant: one side omitting payload, configuration
    or another supplied condition must not silently erase that distinction.
    Condition intervals cannot establish exact operating-point equivalence.
    """
    actual, required = _conditions(actual), _conditions(required)
    if not actual or not required:
        return {"status": UNKNOWN, "missing": ["conditions"], "mismatches": []}
    missing, mismatches = [], []
    for key in sorted(actual.keys() | required.keys()):
        left, right = actual.get(key), required.get(key)
        if left is None or right is None:
            missing.append(key)
        elif isinstance(left, _Quantity) and isinstance(right, _Quantity):
            if left.dimension != right.dimension:
                raise EngineeringInputError("condition-unit-dimension-mismatch")
            if left.minimum != left.maximum or right.minimum != right.maximum:
                missing.append(key + ".exact-operating-point")
            elif left != right:
                mismatches.append(key)
        elif type(left) is not type(right) or left != right:
            mismatches.append(key)
    return {"status": "mismatch" if mismatches else UNKNOWN if missing else "match",
            "missing": missing, "mismatches": mismatches}


def _snapshot(payload):
    if not isinstance(payload, dict):
        raise EngineeringInputError("input-must-be-an-object")
    try:
        # Round-trip prevents non-JSON objects, shared mutable references and
        # nonfinite values from entering the frozen/recomputable input.
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > 500_000:
            raise EngineeringInputError("input-exceeds-limit")
        return json.loads(raw), hashlib.sha256(raw.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, RecursionError):
        raise EngineeringInputError("input-must-be-finite-json") from None


def _text(value, code):
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise EngineeringInputError(code)
    return value


def _revision(record, key, path, missing):
    value = record.get(key)
    if value is None or value == "":
        missing.append(path)
    elif type(value) is int:
        if value <= 0:
            raise EngineeringInputError("invalid-input-version")
    else:
        _text(value, "invalid-input-version")


def evaluate_requirement(payload, *, formula_version=FORMULA_VERSION):
    """Evaluate a versioned requirement/observation pair as unverified inputs.

    ``reviewedCompliance`` is always unknown here. Only a later server service
    can combine this hashed result with current authenticated review records.
    No payload flag, source label, confidence or reviewer name grants approval.
    """
    snapshot, digest = _snapshot(payload)
    formula = _formula("requirement-comparison", formula_version)
    requirement, observation = snapshot.get("requirement"), snapshot.get("observation")
    if not isinstance(requirement, dict) or not isinstance(observation, dict):
        raise EngineeringInputError("requirement-and-observation-must-be-objects")
    missing, reasons = [], []
    for label, record, id_key in (("requirement", requirement, "requirementId"),
                                  ("observation", observation, "observationId")):
        for key in (id_key, "entityId", "metric"):
            if not record.get(key):
                missing.append(label + "." + key)
            else:
                _text(record[key], "invalid-input-identity")
        _revision(record, "inputVersion", label + ".inputVersion", missing)
    for key in ("entityId", "metric"):
        if requirement.get(key) != observation.get(key):
            reasons.append(key + "-mismatch")
    operator = requirement.get("operator")
    if not isinstance(operator, str) or operator not in {">=", ">", "<=", "<", "==", "between"}:
        raise EngineeringInputError("unsupported-comparison-operator")
    comparison = None
    for label, record in (("requirement", requirement), ("observation", observation)):
        if record.get("quantity") is None:
            missing.append(label + ".quantity")
        else:
            _quantity(record["quantity"])
    if requirement.get("quantity") is not None and observation.get("quantity") is not None:
        comparison = compare_quantities(observation["quantity"], operator, requirement["quantity"])
    context = match_conditions(observation.get("conditions"), requirement.get("conditions"))
    if context["status"] != "match":
        reasons.append("operating-conditions-" + context["status"])
    for key in context["missing"]:
        missing.append("conditions." + key if key != "conditions" else key)
    conflicts = observation.get("conflicts", [])
    if not isinstance(conflicts, list):
        raise EngineeringInputError("conflicts-must-be-an-array")
    if conflicts or observation.get("claimState") in {"conflict", "conflicting"} or observation.get("verificationStatus") == "conflict":
        reasons.append("unresolved-conflict")
    if observation.get("claimState") in {"rejected", "superseded", "withdrawn"}:
        reasons.append("observation-not-current")
    evidence = observation.get("evidenceRefs", [])
    if not isinstance(evidence, list) or any(not isinstance(ref, dict) for ref in evidence):
        raise EngineeringInputError("evidence-references-must-be-objects")
    # Missing evidence does not change arithmetic. It remains a separate
    # blocker for reviewed compliance, which this pure function cannot grant.
    mathematical = comparison["result"] if comparison and not missing and not reasons else UNKNOWN
    if not evidence:
        missing.append("observation.evidenceRefs")
    return {"contractVersion": CONTRACT_VERSION, "formula": formula,
            "formulaId": formula["formulaId"], "formulaVersion": formula["version"], "inputHash": digest,
            "inputSnapshot": snapshot, "numericComparison": comparison,
            "mathResult": mathematical, "mathematicalResult": mathematical, "conditionMatch": context,
            "assumptions": deepcopy(formula["assumptions"]),
            "reviewedCompliance": UNKNOWN, "reviewReason": "trusted-server-review-required",
            "truthVerified": False, "engineeringCertification": False,
            "missingInputs": sorted(set(missing)), "reasons": reasons, "evidenceRefs": deepcopy(evidence)}


def _basis(value, path, missing):
    basis = value.get("basis")
    if basis is None:
        missing.append(path + ".basis")
        return
    if not isinstance(basis, dict):
        raise EngineeringInputError("input-basis-must-be-an-object")
    kind = basis.get("kind")
    if kind == "assumption":
        if not basis.get("description"):
            missing.append(path + ".basis.description")
        else:
            _text(basis["description"], "invalid-assumption")
    elif kind in {"reported-specification", "measurement"}:
        for key in ("sourceId", "sourceVersion", "contentHash"):
            if not basis.get(key):
                missing.append(path + ".basis." + key)
            else:
                _text(basis[key], "invalid-source-reference")
    else:
        raise EngineeringInputError("unsupported-input-basis-kind")


def _calculation(payload, name, version):
    snapshot, digest = _snapshot(payload)
    missing = []
    _revision(snapshot, "inputVersion", "inputVersion", missing)
    conditions = _conditions(snapshot.get("conditions"))
    if not conditions:
        missing.append("conditions")
    for key, value in conditions.items():
        if value is None:
            missing.append("conditions." + key)
        elif isinstance(value, _Quantity) and value.minimum != value.maximum:
            missing.append("conditions." + key + ".exact-operating-point")
    assumptions = snapshot.get("assumptions", [])
    if not isinstance(assumptions, list) or len(assumptions) > 100:
        raise EngineeringInputError("assumptions-must-be-a-bounded-array")
    for value in assumptions:
        _text(value, "invalid-assumption")
    return {"contractVersion": CONTRACT_VERSION, "formula": _formula(name, version),
            "formulaId": name, "formulaVersion": version,
            "inputHash": digest, "inputSnapshot": snapshot, "status": UNKNOWN,
            "normalizedInputs": {}, "result": None, "mathResult": None, "missingInputs": missing,
            "assumptions": deepcopy(_FORMULAS[name]["assumptions"]) + deepcopy(assumptions), "claimKind": "concept-estimate",
            "truthVerified": False, "reviewedCompliance": UNKNOWN, "engineeringCertification": False}


def _calculation_quantity(record, key, dimension, path, output):
    value = record.get(key)
    if value is None:
        output["missingInputs"].append(path)
        return None
    quantity = _quantity(value, dimension)
    _basis(value, path, output["missingInputs"])
    output["normalizedInputs"][path] = _render(quantity)
    return quantity


def _fraction(payload, key, output):
    value = payload.get(key)
    if value is None:
        output["missingInputs"].append(key)
        return None
    result = _number(value)
    if result > 1:
        raise EngineeringInputError("fraction-must-be-between-zero-and-one")
    output["normalizedInputs"][key] = _output_number(result)
    return result


def mass_budget(payload, *, formula_version=FORMULA_VERSION):
    """Conservative mass total, concept allowance and limit headroom, in kg."""
    output = _calculation(payload, "mass-budget", formula_version)
    source = output["inputSnapshot"]
    components = source.get("components")
    if components is None or components == []:
        output["missingInputs"].append("components")
        components = []
    if not isinstance(components, list) or len(components) > 1000:
        raise EngineeringInputError("components-must-be-a-bounded-array")
    quantities, ids = [], set()
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            raise EngineeringInputError("component-must-be-an-object")
        component_id = component.get("componentId")
        if not component_id:
            output["missingInputs"].append(f"components[{index}].componentId")
        else:
            _text(component_id, "invalid-component-id")
            if component_id in ids:
                raise EngineeringInputError("duplicate-component-id")
            ids.add(component_id)
        quantity = _calculation_quantity(component, "mass", "mass", f"components[{index}].mass", output)
        if quantity is not None:
            quantities.append(quantity)
    limit = _calculation_quantity(source, "massLimit", "mass", "massLimit", output)
    contingency = _fraction(source, "contingencyFraction", output)
    output["missingInputs"] = sorted(set(output["missingInputs"]))
    if output["missingInputs"]:
        return output
    total = _Quantity(sum((q.minimum for q in quantities), Fraction()),
                      sum((q.maximum for q in quantities), Fraction()), "mass", "kg")
    budget = _Quantity(total.minimum * (1 + contingency), total.maximum * (1 + contingency), "mass", "kg")
    # Headroom is a signed derived result; negative inputs remain prohibited.
    headroom = _Quantity(limit.minimum - budget.maximum, limit.maximum - budget.minimum, "mass", "kg")
    result = {"componentMass": _render(total), "budgetedMass": _render(budget),
              "headroom": _render(headroom), "limitComparison": _compare(budget, "<=", limit)}
    output.update(status="calculated", result=result, mathResult=deepcopy(result))
    return output


def endurance_budget(payload, *, formula_version=FORMULA_VERSION):
    """Energy / average-power estimate only; no model-derived performance."""
    output = _calculation(payload, "energy-endurance", formula_version)
    source = output["inputSnapshot"]
    energy = _calculation_quantity(source, "energy", "energy", "energy", output)
    power = _calculation_quantity(source, "averagePower", "power", "averagePower", output)
    usable = _fraction(source, "usableFraction", output)
    reserve = _fraction(source, "reserveFraction", output)
    # Both inputs must state the same system boundary. An electrical battery
    # energy cannot be silently divided by mechanical shaft power, for example.
    boundary = source.get("systemBoundary")
    if boundary is None or boundary == "":
        output["missingInputs"].append("systemBoundary")
    else:
        _text(boundary, "invalid-system-boundary")
    if power is not None and power.minimum <= 0:
        raise EngineeringInputError("average-power-must-be-strictly-positive")
    output["missingInputs"] = sorted(set(output["missingInputs"]))
    if output["missingInputs"]:
        return output
    factor = usable * (1 - reserve)
    usable_energy = _Quantity(energy.minimum * factor, energy.maximum * factor, "energy", "Wh")
    # Wh / W yields hours. Store canonical seconds internally for consistent
    # dimensional rendering, then explicitly render the requested hours.
    duration = _Quantity(usable_energy.minimum / power.maximum * 3600,
                         usable_energy.maximum / power.minimum * 3600, "time", "s")
    result = {"usableEnergy": _render(usable_energy), "endurance": _render(duration, "h"), "systemBoundary": boundary}
    output.update(status="calculated", result=result, mathResult=deepcopy(result))
    return output


# Explicit future tool contracts; these are inert schema descriptions. The
# parent integrator owns registration, immutable tool versions and Run storage.
_BASIS_SCHEMA = {"type": "object", "required": ["kind"], "properties": {
    "kind": {"enum": ["assumption", "reported-specification", "measurement"]},
    "description": {"type": "string", "minLength": 1, "maxLength": 1000},
    "sourceId": {"type": "string", "minLength": 1, "maxLength": 1000},
    "sourceVersion": {"type": "string", "minLength": 1, "maxLength": 1000},
    "contentHash": {"type": "string", "minLength": 1, "maxLength": 1000}}, "additionalProperties": False}
QUANTITY_SCHEMA = {"type": "object", "required": ["unit"], "properties": {
    "value": {"type": "number", "minimum": 0}, "min": {"type": "number", "minimum": 0},
    "max": {"type": "number", "minimum": 0}, "unit": {"enum": list(_UNITS)}, "basis": deepcopy(_BASIS_SCHEMA)},
    "oneOf": [{"required": ["value"], "not": {"anyOf": [{"required": ["min"]}, {"required": ["max"]}]}},
              {"required": ["min", "max"], "not": {"required": ["value"]}}], "additionalProperties": False}
_CONDITIONS_SCHEMA = {"type": ["object", "null"], "maxProperties": 50, "additionalProperties": {
    "oneOf": [{"type": "string", "maxLength": 1000}, {"type": "null"}, deepcopy(QUANTITY_SCHEMA)]}}
_VERSION_SCHEMA = {"oneOf": [{"type": "string", "minLength": 1, "maxLength": 1000}, {"type": "integer", "minimum": 1}]}
_IDENTITY_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 1000}
_COMMON_PROPERTIES = {"inputVersion": deepcopy(_VERSION_SCHEMA), "conditions": deepcopy(_CONDITIONS_SCHEMA),
    "assumptions": {"type": "array", "maxItems": 100, "items": {"type": "string", "minLength": 1, "maxLength": 1000}}}
_OBSERVATION_SCHEMA = {"type": "object", "properties": {
    "observationId": deepcopy(_IDENTITY_SCHEMA), "entityId": deepcopy(_IDENTITY_SCHEMA), "metric": deepcopy(_IDENTITY_SCHEMA),
    "inputVersion": deepcopy(_VERSION_SCHEMA), "quantity": {"oneOf": [deepcopy(QUANTITY_SCHEMA), {"type": "null"}]},
    "conditions": deepcopy(_CONDITIONS_SCHEMA), "evidenceRefs": {"type": "array", "items": {"type": "object"}},
    "conflicts": {"type": "array"}, "claimState": {"type": "string"}, "truthVerified": {"type": "boolean"},
    "reviewStatus": {"type": "string"}, "verificationStatus": {"type": "string"}, "sourceKind": {"type": "string"}},
    "additionalProperties": False}
_REQUIREMENT_SCHEMA = {"type": "object", "required": ["operator"], "properties": {
    "requirementId": deepcopy(_IDENTITY_SCHEMA), "entityId": deepcopy(_IDENTITY_SCHEMA), "metric": deepcopy(_IDENTITY_SCHEMA),
    "inputVersion": deepcopy(_VERSION_SCHEMA), "operator": {"enum": [">=", ">", "<=", "<", "==", "between"]},
    "quantity": {"oneOf": [deepcopy(QUANTITY_SCHEMA), {"type": "null"}]}, "conditions": deepcopy(_CONDITIONS_SCHEMA)},
    "additionalProperties": False}
TOOL_INPUT_SCHEMAS = {
    "engineering.evaluate_requirement": {"type": "object", "required": ["requirement", "observation"],
        "properties": {"requirement": _REQUIREMENT_SCHEMA, "observation": _OBSERVATION_SCHEMA}, "additionalProperties": False},
    "engineering.mass_budget": {"type": "object", "properties": {**deepcopy(_COMMON_PROPERTIES),
        "components": {"type": "array", "maxItems": 1000, "items": {"type": "object", "properties": {
            "componentId": deepcopy(_IDENTITY_SCHEMA), "mass": deepcopy(QUANTITY_SCHEMA)}, "additionalProperties": False}},
        "massLimit": deepcopy(QUANTITY_SCHEMA), "contingencyFraction": {"type": "number", "minimum": 0, "maximum": 1}},
        "additionalProperties": False},
    "engineering.endurance_budget": {"type": "object", "properties": {**deepcopy(_COMMON_PROPERTIES),
        "energy": deepcopy(QUANTITY_SCHEMA), "averagePower": deepcopy(QUANTITY_SCHEMA),
        "usableFraction": {"type": "number", "minimum": 0, "maximum": 1},
        "reserveFraction": {"type": "number", "minimum": 0, "maximum": 1},
        "systemBoundary": deepcopy(_IDENTITY_SCHEMA)}, "additionalProperties": False},
}
TOOL_OUTPUT_SCHEMAS = {
    key: {"type": "object", "required": ["contractVersion", "formula", "inputHash", "inputSnapshot", "missingInputs",
                                          "truthVerified", "reviewedCompliance", "engineeringCertification"],
          "properties": {"contractVersion": {"const": CONTRACT_VERSION}, "truthVerified": {"const": False},
                         "engineeringCertification": {"const": False}, "reviewedCompliance": {"const": UNKNOWN}}}
    for key in TOOL_INPUT_SCHEMAS
}
