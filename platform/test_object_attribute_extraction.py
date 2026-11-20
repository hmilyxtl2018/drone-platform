"""Per-object requests preserve identity and strictly locate proposed values."""

from copy import deepcopy
import hashlib
import importlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

try:
    from object_attribute_extraction import enrich_object_attributes, MAX_CONTEXT_CHARACTERS, _recover_explicit_acronym_context, _object_context
    from object_verification import build_object_verification_checks
    from research_trace import execution_context
except ModuleNotFoundError:  # pragma: no cover
    from .object_attribute_extraction import enrich_object_attributes, MAX_CONTEXT_CHARACTERS, _recover_explicit_acronym_context, _object_context
    from .object_verification import build_object_verification_checks
    from .research_trace import execution_context


def source(body, item_id="source-a", **context):
    return {"itemId": item_id, "title": "Public technology note", "body": body,
            "sourceUri": f"https://example.test/{item_id}", "sourceVersion": "v1",
            "contentHash": hashlib.sha256(body.encode()).hexdigest(), "publishedAt": "2026-01-01",
            **context}


def node(material, label="Camera X", kind="EquipmentModel", identity_quote=None, **payload):
    quote = identity_quote or material["body"]
    start = material["body"].index(quote)
    return {"id": "object:" + label.replace(" ", "-"), "label": label, "type": kind,
            "iri": "https://example.test/ontology/" + label.replace(" ", "-"),
            "evidenceIds": [material["itemId"]],
            "payload": {"extractionMethod": "llm", "reviewStatus": "pending",
                        "confidence": "low", "attributes": [],
                        "evidenceRefs": [{"itemId": material["itemId"], "quote": quote,
                                          "startChar": start, "endChar": start + len(quote),
                                          "sourceUri": material["sourceUri"], "sourceVersion": material["sourceVersion"],
                                          "contentHash": material["contentHash"]}], **payload}}


def attr(material, quote, value="open interface", name="接口方式", **extra):
    return {"name": name, "value": value, "itemId": material["itemId"], "quote": quote, **extra}


def prompt_data(messages):
    return json.loads(messages[1]["content"].split("\n", 1)[1])


class ObjectAttributeExtractionTests(unittest.TestCase):
    def validate_contract(self, definition, value):
        root = Path(__file__).resolve().parents[1] / "ontology/coast-raid-k"
        schema = json.loads((root / "schema.object-attributes.json").read_text(encoding="utf-8"))
        common = json.loads((root / "schema.common.json").read_text(encoding="utf-8"))
        registry = Registry().with_resources([(schema["$id"], Resource.from_contents(schema)),
                                               (common["$id"], Resource.from_contents(common))])
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator({"$ref": schema["$id"] + "#/$defs/" + definition}, registry=registry)
        validator.validate(value)

    def test_object_context_can_select_a_source_after_the_thirtieth_material(self):
        materials = [source("Unrelated source text.", f"source-{index}") for index in range(31)]
        late = materials[-1]
        late["body"] = "Camera X is a public demonstration platform."
        late["contentHash"] = hashlib.sha256(late["body"].encode()).hexdigest()
        original = node(late)
        identity_refs = [{"itemId": late["itemId"], "startChar": 0, "endChar": len(late["body"]), "quote": late["body"]}]
        rows, sources, _ = _object_context(original, materials, identity_refs, "EquipmentModel")
        self.assertEqual([row["itemId"] for row in rows], [late["itemId"]])
        self.assertIn(late["itemId"], sources)

    def test_late_named_definition_is_sent_and_accepted_without_identity_change(self):
        identity = "Camera X is a public demonstration platform."
        quote = "Camera X uses an open interface in a controlled laboratory test."
        material = source(identity + "\n\n" + "Navigation\n" * 1200 + "\n\n" + quote,
                          contentNature="manufacturer-claim")
        original = node(material, identity_quote=identity)
        original["payload"]["modelMetadata"] = {"model": "recorded-model", "tokens": 80}
        before = deepcopy(original)
        calls = []
        def requester(messages):
            data = prompt_data(messages)
            calls.append(data)
            windows = data["materials"][0]["excerpts"]
            self.assertTrue(any(quote in window["text"] for window in windows))
            self.assertLessEqual(sum(len(window["text"]) for row in data["materials"] for window in row["excerpts"]), MAX_CONTEXT_CHARACTERS)
            return {"objectId": original["id"], "attributes": [attr(material, quote, attributeKey="interface-standard",
                                                                    displayValue="开放接口", conditions="controlled laboratory test")], "gaps": []}
        result = enrich_object_attributes([material], [original], requester, model="recorded-model")
        enriched = result["nodes"][0]
        self.assertEqual(original, before)
        for key in ("id", "label", "type", "iri"):
            self.assertEqual(enriched[key], original[key])
        self.assertEqual(enriched["payload"]["evidenceRefs"], original["payload"]["evidenceRefs"])
        self.assertEqual(enriched["payload"]["modelMetadata"], original["payload"]["modelMetadata"])
        attribute = enriched["payload"]["attributes"][0]
        self.assertEqual(attribute["value"], "open interface")
        self.assertEqual(attribute["rawValue"], "open interface")
        self.assertEqual(attribute["normalizedValue"], "开放接口")
        self.assertFalse(attribute["normalizationVerified"])
        self.assertEqual(attribute["claimKind"], "manufacturer-claim")
        self.assertIsNone(attribute["ontologyPredicate"])
        self.assertEqual(attribute["state"], "pending-review")
        ref = attribute["evidenceRefs"][0]
        self.assertEqual(material["body"][ref["startChar"]:ref["endChar"]], quote)
        self.assertGreater(ref["startChar"], 5000)
        self.assertEqual(enriched["payload"]["attributeExtraction"]["retainedCount"], 1)
        self.assertGreater(enriched["payload"]["attributeExtraction"]["coverage"]["unreadCharacters"], 0)
        checks = build_object_verification_checks({"assessmentId": "test", "sourceSnapshot": [material]}, {"nodes": [enriched], "edges": []})
        self.assertEqual(checks[0]["machineStatus"], "passed")
        self.assertFalse(checks[0]["truthVerified"])

    def test_other_object_statement_is_rejected_even_when_identity_occurs_in_quote(self):
        quote = "Camera X is a platform. Camera Y supports an open interface."
        material = source(quote)
        objects = [node(material, "Camera X"), node(material, "Camera Y")]
        def request(messages):
            data = prompt_data(messages)
            return {"objectId": data["object"]["objectId"], "attributes": [attr(material, quote)]}
        result = enrich_object_attributes([material], objects, request, object_ids=[objects[0]["id"]])
        self.assertEqual(result["nodes"][0]["payload"]["attributes"], [])
        status = result["nodes"][0]["payload"]["attributeExtraction"]
        self.assertEqual(status["rejectedReasonCounts"], {"other-object-value-context": 1})
        self.assertEqual(status["rejectedProposals"][0]["quote"], quote)
        self.assertEqual(status["rejectedProposals"][0]["state"], "rejected-diagnostic")
        self.assertEqual(result["metadata"]["calls"], 1)

    def test_missing_object_in_property_quote_fails_and_complete_pronoun_paragraph_passes(self):
        quote = "Camera X is a platform. It retains human oversight."
        material = source(quote)
        original = node(material)
        result = enrich_object_attributes([material], [original], lambda _: {"attributes": [
            attr(material, "It retains human oversight.", "human oversight"),
            attr(material, quote, "human oversight", name="人工监督", attributeKey="human-oversight")], "gaps": []})
        self.assertEqual(result["metadata"]["retainedAttributes"], 1)
        self.assertEqual(result["metadata"]["rejectedAttributes"], 1)
        self.assertEqual(result["nodes"][0]["payload"]["attributeExtraction"]["rejectedReasonCounts"], {"unsupported-object-context": 1})

    def test_image_discovery_metadata_is_not_a_property_source(self):
        material = source("Camera X is a platform.")
        image = source("Camera X supports an open interface.", "image-a", provenance={"contentKind": "image-search-metadata"})
        seen = []
        def request(messages):
            seen.extend(row["itemId"] for row in prompt_data(messages)["materials"])
            return {"attributes": [attr(image, image["body"])]}
        result = enrich_object_attributes([image, material], [node(material)], request)
        self.assertEqual(seen, ["source-a"])
        self.assertEqual(result["metadata"]["excludedDiscoveryOnlyItems"], 1)
        self.assertEqual(result["metadata"]["retainedAttributes"], 0)
        self.assertEqual(result["nodes"][0]["payload"]["attributeExtraction"]["rejectedReasonCounts"], {"unknown-source": 1})

    def test_partial_failure_preserves_existing_attributes_and_later_objects_continue(self):
        material = source("Camera X uses an open interface. Camera Y retains human oversight.")
        originals = [node(material, "Camera X"), node(material, "Camera Y")]
        old = {"name": "已有", "value": "original", "reviewStatus": "pending"}
        originals[0]["payload"]["attributes"] = [deepcopy(old)]
        calls = []
        def request(messages):
            data = prompt_data(messages)
            calls.append(data["object"]["objectId"])
            if len(calls) == 1:
                raise RuntimeError("pretend-sensitive-credential")
            return {"attributes": [attr(material, material["body"], "human oversight", "监督")]}
        result = enrich_object_attributes([material], originals, request)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["nodes"][0]["payload"]["attributes"], [old])
        self.assertEqual(result["nodes"][0]["payload"]["attributeExtraction"]["status"], "failed")
        self.assertEqual(result["nodes"][1]["payload"]["attributeExtraction"]["status"], "completed")
        self.assertNotIn("pretend-sensitive-credential", json.dumps(result))

    def test_bounds_and_remaining_batch_do_not_repeat_processed_objects(self):
        text = "\n\n".join(f"Camera {index} uses an open interface." for index in range(10))
        material = source(text)
        objects = [node(material, f"Camera {index}") for index in range(10)]
        seen = []
        def request(messages):
            data = prompt_data(messages)
            seen.append(data["object"]["objectId"])
            return {"objectId": data["object"]["objectId"], "attributes": []}
        first = enrich_object_attributes([material], objects, request)
        self.assertEqual(len(seen), 8)
        self.assertEqual(len(first["metadata"]["remainingObjectIds"]), 2)
        second = enrich_object_attributes([material], first["nodes"], request, object_ids=first["metadata"]["remainingObjectIds"])
        self.assertEqual(len(seen), 10)
        self.assertEqual(second["nodes"][0]["payload"]["attributeExtraction"]["status"], "no-explicit-attributes")
        self.assertEqual(second["metadata"]["calls"], 2)
        self.assertEqual(second["metadata"]["remainingObjectIds"], [])

    def test_output_truncation_retries_only_same_object_with_unchanged_materials(self):
        material = source("Camera X uses an open interface.")
        original = node(material)
        previous = {"name": "已有属性", "value": "historical"}
        original["payload"]["attributes"] = [deepcopy(previous)]
        attempts = []
        def truncated(messages):
            attempts.append(deepcopy(messages))
            raise ValueError("provider output exhausted its token budget")
        def recovered(messages):
            attempts.append(deepcopy(messages))
            return {"objectId": original["id"], "attributes": [attr(material, material["body"])]}
        result = enrich_object_attributes([material], [original], truncated, retry_request_json=recovered)
        row = result["metadata"]["perObject"][0]
        self.assertEqual([prompt_data(messages) for messages in attempts], [prompt_data(attempts[0])] * 2)
        self.assertEqual(attempts[0][1], attempts[1][1])
        self.assertIn(attempts[0][0]["content"], attempts[1][0]["content"])
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["calls"], 2)
        self.assertEqual(row["outputTruncationRetries"], 1)
        self.assertEqual(result["metadata"]["calls"], 2)
        self.assertEqual(result["metadata"]["attemptedObjects"], 1)
        self.assertEqual(result["metadata"]["outputTruncationRetries"], 1)
        self.assertEqual(result["metadata"]["retryableObjectIds"], [])
        self.assertEqual(result["nodes"][0]["payload"]["attributes"][0], previous)
        self.assertEqual(len(result["nodes"][0]["payload"]["attributes"]), 2)
        self.validate_contract("PerObjectExtractionMetadata", row)

    def test_repeated_output_truncation_is_bounded_preserves_old_values_and_continues(self):
        material = source("Camera X uses an open interface. Camera Y retains human oversight.")
        originals = [node(material, "Camera X"), node(material, "Camera Y")]
        previous = {"name": "已有属性", "value": "historical"}
        originals[0]["payload"]["attributes"] = [deepcopy(previous)]
        attempts = []
        def request(messages):
            object_id = prompt_data(messages)["object"]["objectId"]
            attempts.append(object_id)
            if object_id == originals[0]["id"]:
                raise ValueError("provider output exhausted its token budget")
            return {"objectId": object_id, "attributes": []}
        result = enrich_object_attributes([material], originals, request, replace_existing=True)
        self.assertEqual(attempts, [originals[0]["id"], originals[0]["id"], originals[1]["id"]])
        self.assertEqual(result["nodes"][0]["payload"]["attributes"], [previous])
        self.assertEqual(result["metadata"]["perObject"][0]["status"], "failed")
        self.assertEqual(result["metadata"]["perObject"][1]["status"], "no-explicit-attributes")
        self.assertEqual(result["metadata"]["retryableObjectIds"], [originals[0]["id"]])
        self.assertEqual(result["metadata"]["calls"], 3)
        self.assertEqual(result["metadata"]["attemptedObjects"], 2)

    def test_truncation_retry_does_not_consume_eight_object_budget(self):
        material = source("\n\n".join(f"Camera {index} uses an open interface." for index in range(10)))
        originals = [node(material, f"Camera {index}") for index in range(10)]
        attempts = []
        def request(messages):
            object_id = prompt_data(messages)["object"]["objectId"]
            attempts.append(object_id)
            if len(attempts) == 1:
                raise ValueError("provider output exhausted its token budget")
            return {"objectId": object_id, "attributes": []}
        result = enrich_object_attributes([material], originals, request)
        self.assertEqual(attempts, [originals[0]["id"]] + [obj["id"] for obj in originals[:8]])
        self.assertEqual(result["metadata"]["calls"], 9)
        self.assertEqual(result["metadata"]["attemptedObjects"], 8)
        self.assertEqual(result["metadata"]["remainingObjectIds"], [obj["id"] for obj in originals[8:]])

    def test_non_truncation_value_error_is_not_retried(self):
        material = source("Camera X uses an open interface.")
        attempts = []
        def request(messages):
            attempts.append(messages)
            raise ValueError("invalid provider response containing private text")
        result = enrich_object_attributes([material], [node(material)], request)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(result["metadata"]["outputTruncationRetries"], 0)
        self.assertEqual(result["metadata"]["perObject"][0]["status"], "failed")
        self.assertNotIn("private text", json.dumps(result))

    def test_truncated_transport_and_recovery_both_keep_actual_call_statistics(self):
        try:
            from intelligence_agent import DeepSeekClaimProvider
        except ModuleNotFoundError:  # pragma: no cover
            from .intelligence_agent import DeepSeekClaimProvider
        material = source("Camera X uses an open interface.")
        original = node(material)
        requests, events = [], []
        class Opener:
            def open(self, request, timeout):
                request_data = json.loads(request.data)
                requests.append(request_data)
                truncated = len(requests) == 1
                result = {"objectId": original["id"], "attributes": [] if truncated else [attr(material, material["body"])]}
                return io.BytesIO(json.dumps({"model": "deepseek-v4-pro", "choices": [{
                    "finish_reason": "length" if truncated else "stop", "message": {"content": json.dumps(result)}}],
                    "usage": {"prompt_tokens": 234, "completion_tokens": 4096 if truncated else 300}}).encode())
        provider = DeepSeekClaimProvider(api_key="private-test-key", model="deepseek-v4-pro")
        module = importlib.import_module(DeepSeekClaimProvider.__module__)
        with patch.object(module.urllib.request, "build_opener", return_value=Opener()):
            with execution_context(events.append, task_id="recovery-task"):
                result = enrich_object_attributes([material], [original],
                    lambda messages: provider._request_json(messages, thinking=False, output_token_limit=4096),
                    retry_request_json=lambda messages: provider._request_json(messages, thinking=False, output_token_limit=8192))
        self.assertEqual([request["max_tokens"] for request in requests], [4096, 8192])
        self.assertTrue(all(request["thinking"] == {"type": "disabled"} for request in requests))
        self.assertEqual(requests[0]["messages"][1], requests[1]["messages"][1])
        calls = [event for event in events if event["operation"] == "chat-completions" and event["status"] != "started"]
        self.assertEqual([event["status"] for event in calls], ["failed", "succeeded"])
        self.assertEqual([event["details"]["outputTokens"] for event in calls], [4096, 300])
        self.assertEqual([event["details"]["finishReason"] for event in calls], ["length", "stop"])
        self.assertEqual(events[-1]["details"]["retryCount"], 1)
        self.assertEqual(result["metadata"]["perObject"][0]["status"], "completed")
        self.assertNotIn("private-test-key", json.dumps(events))
        self.assertNotIn(material["body"], json.dumps(events))

    def test_full_refresh_replaces_current_values_but_supplement_and_failure_preserve_them(self):
        material = source("Camera X uses an open interface.")
        original = node(material)
        previous = {"assertionId": "old-interface", "name": "接口", "value": "open interface"}
        original["payload"]["attributes"] = [deepcopy(previous)]
        empty = lambda _: {"attributes": []}
        supplement = enrich_object_attributes([material], [original], empty)
        self.assertEqual(supplement["nodes"][0]["payload"]["attributes"], [previous])
        refreshed = enrich_object_attributes([material], [original], empty, replace_existing=True)
        self.assertEqual(refreshed["nodes"][0]["payload"]["attributes"], [])
        self.assertEqual(refreshed["nodes"][0]["payload"]["attributeExtraction"]["status"], "no-explicit-attributes")
        self.assertEqual(original["payload"]["attributes"], [previous])
        def failed(_):
            raise TimeoutError("provider failed")
        result = enrich_object_attributes([material], [original], failed, replace_existing=True)
        self.assertEqual(result["nodes"][0]["payload"]["attributes"], [previous])
        self.assertEqual(result["nodes"][0]["payload"]["attributeExtraction"]["status"], "failed")
        rebuilt = enrich_object_attributes([material], [original], lambda _: {
            "attributes": [attr(material, material["body"])]}, replace_existing=True)
        self.assertEqual(len(rebuilt["nodes"][0]["payload"]["attributes"]), 1)
        self.assertNotEqual(rebuilt["nodes"][0]["payload"]["attributes"][0]["assertionId"], "old-interface")

    def test_attribute_limit_duplicate_and_diagnostic_bounds(self):
        material = source("Camera X exposes value " + " ".join(str(i) for i in range(15)) + ".")
        original = node(material)
        proposals = [attr(material, material["body"], str(i), f"属性{i}") for i in range(15)]
        result = enrich_object_attributes([material], [original], lambda _: {"attributes": proposals})
        self.assertEqual(len(result["nodes"][0]["payload"]["attributes"]), 12)
        self.assertEqual(result["metadata"]["rejectedAttributes"], 3)
        self.assertLessEqual(len(result["nodes"][0]["payload"]["attributeExtraction"]["rejectedProposals"]), 12)
        duplicate = enrich_object_attributes([material], [original], lambda _: {"attributes": [proposals[0], proposals[0]]})
        self.assertEqual(duplicate["metadata"]["duplicateAttributes"], 1)

    def test_public_uav_platform_metrics_are_retained_with_explicit_fields(self):
        quote = ("UAV-X is an unmanned aircraft. UAV-X endurance is 24 hours, flight range is 100 km, payload capacity is 20 kg, "
                 "communications use Link 16, and autonomy is supervised.")
        material = source(quote, contentNature="manufacturer-disclosure")
        original = node(material, label="UAV-X", identity_quote="UAV-X is an unmanned aircraft.")
        proposals = [
            attr(material, quote, "24", "续航时间", attributeKey="endurance-duration", unit="hours"),
            attr(material, quote, "100", "飞行航程", attributeKey="flight-range", unit="km"),
            attr(material, quote, "20", "载荷容量", attributeKey="payload-capacity", unit="kg"),
            attr(material, quote, "Link 16", "通信链路", attributeKey="communications-link"),
            attr(material, quote, "supervised", "自主模式", attributeKey="autonomy-mode"),
        ]
        result = enrich_object_attributes([material], [original], lambda _: {"attributes": proposals, "gaps": []})
        attrs = result["nodes"][0]["payload"]["attributes"]
        self.assertEqual(result["metadata"]["retainedAttributes"], 5)
        self.assertEqual({item["attributeKey"] for item in attrs}, {
            "endurance-duration", "flight-range", "payload-capacity", "communications-link", "autonomy-mode",
        })
        self.assertEqual(attrs[0]["unit"], "hours")
        self.assertFalse(attrs[0]["truthVerified"])

    def test_weapon_and_targeting_context_is_not_saved_as_platform_capability(self):
        quote = ("UAV-X is an unmanned aircraft. UAV-X has a weapon payload of 20 kg, weapon engagement range is 100 km, "
                 "and autonomous target selection is enabled.")
        material = source(quote)
        original = node(material, label="UAV-X", identity_quote="UAV-X is an unmanned aircraft.")
        proposals = [
            attr(material, quote, "20", "载荷", attributeKey="payload-capacity", unit="kg"),
            attr(material, quote, "100", "航程", attributeKey="flight-range", unit="km"),
            attr(material, quote, "enabled", "自主模式", attributeKey="autonomy-mode"),
        ]
        result = enrich_object_attributes([material], [original], lambda _: {"attributes": proposals, "gaps": []})
        self.assertEqual(result["metadata"]["retainedAttributes"], 0)
        self.assertEqual(result["nodes"][0]["payload"]["attributeExtraction"]["rejectedReasonCounts"], {"operational-employment-context": 3})

    def test_same_value_from_two_sources_merges_exact_evidence_without_trust_promotion(self):
        quote = "Camera X uses an open interface."
        source_a = source(quote, "source-a")
        source_b = source(quote, "source-b", sourceVersion="v2", contentNature="manufacturer-claim")
        sources = [source_a, source_b]
        proposals = [attr(source_a, quote), attr(source_b, quote), attr(source_b, quote)]
        original = node(source_a)
        result = enrich_object_attributes(sources, [original], lambda _: {"attributes": proposals})
        enriched = result["nodes"][0]
        self.assertEqual(len(enriched["payload"]["attributes"]), 1)
        attribute = enriched["payload"]["attributes"][0]
        self.assertEqual(len(attribute["evidenceRefs"]), 2)
        self.assertEqual([(ref["itemId"], ref["sourceVersion"]) for ref in attribute["evidenceRefs"]], [("source-a", "v1"), ("source-b", "v2")])
        self.assertEqual(attribute["independenceStatus"], "unknown")
        self.assertFalse(attribute["truthVerified"])
        self.assertEqual(attribute["reviewStatus"], "pending")
        self.assertEqual(enriched["evidenceIds"], ["source-a", "source-b"])
        self.assertEqual(enriched["payload"]["attributeExtraction"]["mergedEvidenceRefCount"], 1)
        self.assertEqual(result["metadata"]["duplicateAttributes"], 2)
        self.assertNotIn("independentSourceCount", result["metadata"])
        self.validate_contract("RetainedAttributeDraft", attribute)
        self.validate_contract("PerObjectExtractionMetadata", enriched["payload"]["attributeExtraction"])
        checks = build_object_verification_checks({"assessmentId": "test", "sourceSnapshot": sources}, {"nodes": [enriched], "edges": []})
        self.assertEqual(checks[0]["machineStatus"], "passed")
        self.assertEqual(original["payload"]["attributes"], [])

    def test_existing_attribute_evidence_is_enriched_on_detached_copy_and_same_citation_stays_single(self):
        quote = "Camera X uses an open interface."
        source_a, source_b = source(quote), source(quote, "source-b", sourceVersion="v2")
        first = enrich_object_attributes([source_a], [node(source_a)], lambda _: {"attributes": [attr(source_a, quote)]})
        original = first["nodes"][0]
        before = deepcopy(original)
        second = enrich_object_attributes([source_a, source_b], [original], lambda _: {"attributes": [attr(source_b, quote), attr(source_b, quote)]})
        self.assertEqual(original, before)
        attr_before = original["payload"]["attributes"][0]
        attr_after = second["nodes"][0]["payload"]["attributes"][0]
        self.assertEqual(attr_after["assertionId"], attr_before["assertionId"])
        self.assertEqual(len(attr_after["evidenceRefs"]), 2)
        self.assertEqual(second["metadata"]["retainedAttributes"], 0)
        self.assertEqual(second["metadata"]["mergedEvidenceRefCount"], 1)
        self.assertEqual(second["nodes"][0]["payload"]["attributeExtraction"]["status"], "completed")
        third = enrich_object_attributes([source_a, source_b], second["nodes"], lambda _: {"attributes": [attr(source_b, quote)]})
        self.assertEqual(third["metadata"]["mergedEvidenceRefCount"], 0)
        self.assertEqual(len(third["nodes"][0]["payload"]["attributes"][0]["evidenceRefs"]), 2)

    def test_unknown_fields_are_not_claimed_as_source_nondisclosure(self):
        material = source("Camera X is a public platform.")
        result = enrich_object_attributes([material], [node(material)], lambda _: {"attributes": [], "gaps": ["Only identity given in these windows."]})
        row = result["nodes"][0]["payload"]["attributeExtraction"]
        self.assertEqual(row["status"], "no-explicit-attributes")
        self.assertFalse(row["coverage"]["complete"])
        self.assertFalse(row["coverage"]["noDisclosureConfirmed"])
        self.assertTrue(all(field["state"] == "not-extracted" for field in row["coverage"]["fieldStates"]))
        self.assertTrue(row["gaps"])
        self.validate_contract("PerObjectExtractionMetadata", row)

    def test_valid_time_retains_only_literal_fact_qualifier_and_unknown_is_explicit_null(self):
        material = source("Camera X uses an open interface during the 2026 laboratory programme.")
        good = attr(material, material["body"], validTime="during the 2026 laboratory programme")
        result = enrich_object_attributes([material], [node(material)], lambda _: {"attributes": [good]})
        retained = result["nodes"][0]["payload"]["attributes"][0]
        self.assertEqual(retained["validTime"], "during the 2026 laboratory programme")
        self.validate_contract("RetainedAttributeDraft", retained)
        self.validate_contract("PerObjectExtractionMetadata", result["nodes"][0]["payload"]["attributeExtraction"])
        unknown = enrich_object_attributes([material], [node(material)], lambda _: {"attributes": [attr(material, material["body"])]})
        self.assertIn("validTime", unknown["nodes"][0]["payload"]["attributes"][0])
        self.assertIsNone(unknown["nodes"][0]["payload"]["attributes"][0]["validTime"])
        self.validate_contract("RetainedAttributeDraft", unknown["nodes"][0]["payload"]["attributes"][0])
        for invalid in (material["publishedAt"], "2027", "", True, "x" * 201):
            with self.subTest(validTime=invalid):
                result = enrich_object_attributes([material], [node(material)], lambda _: {"attributes": [attr(material, material["body"], validTime=invalid)]})
                self.assertEqual(result["metadata"]["retainedAttributes"], 0)
                row = result["nodes"][0]["payload"]["attributeExtraction"]
                self.assertEqual(row["rejectedReasonCounts"], {"unsupported-qualifier": 1})
                self.validate_contract("PerObjectExtractionMetadata", row)
                self.validate_contract("RejectedProposalDiagnostic", row["rejectedProposals"][0])

    def test_saved_real_mars_draft_and_rejected_object_context_follow_machine_contract(self):
        root = Path(__file__).resolve().parents[1]
        fixture = json.loads((root / "docs/fixtures/osint-uav-attributes-baseline-20261008.json").read_text(encoding="utf-8"))
        material = fixture["selectedMaterial"]
        known = fixture["knownExtractableAttributes"][0]
        quote = known["evidence"]["quote"]
        original = node(material, "MARS Mission System", "Technology", identity_quote=quote)
        proposal = attr(material, quote, known["rawValue"], known["name"], attributeKey="architecture", displayValue=known["displayValue"])
        result = enrich_object_attributes([material], [original], lambda _: {"attributes": [proposal,
            attr(material, quote, "invented value", "错误属性")]})
        payload = result["nodes"][0]["payload"]
        self.validate_contract("RetainedAttributeDraft", payload["attributes"][0])
        self.validate_contract("PerObjectExtractionMetadata", payload["attributeExtraction"])
        self.validate_contract("ContractEnvelope", {"attributes": payload["attributes"], "perObject": [payload["attributeExtraction"]],
            "packageSnapshot": {"packageId": "coast-raid-k", "version": "test-only", "hash": material["contentHash"]}, "truthVerified": False})
        self.assertIsNone(payload["attributes"][0]["validTime"])
        self.assertEqual(payload["attributeExtraction"]["rejectedCount"], 1)
        altered = deepcopy(payload["attributes"][0])
        altered["truthVerified"] = True
        with self.assertRaises(ValidationError):
            self.validate_contract("RetainedAttributeDraft", altered)
        old = {"name": "架构描述", "value": known["rawValue"], "evidenceRefs": [known["evidence"]]}
        self.validate_contract("LegacyCompatibleAttribute", old)
        with self.assertRaises(ValidationError):
            self.validate_contract("RetainedAttributeDraft", old)

    def test_saved_airbus_short_definition_recovers_one_exact_original_bullet(self):
        root = Path(__file__).resolve().parents[1]
        fixture = json.loads((root / "docs/fixtures/osint-uav-attributes-baseline-20261008.json").read_text(encoding="utf-8"))
        material = fixture["selectedMaterial"]
        known = fixture["knownExtractableAttributes"][0]
        identity_quote = known["evidence"]["quote"]
        # Replay the real short-definition failure shape; this callback is
        # explicitly an offline saved-source test, not a provider run.
        short_quote = identity_quote.split(". The MARS Mission System", 1)[0] + "."
        self.assertIn("(MARS) Mission System", short_quote)
        self.assertNotIn("MARS Mission System", short_quote)
        original = node(material, "MARS Mission System", "Technology", identity_quote=identity_quote)
        result = enrich_object_attributes([material], [original], lambda _: {"attributes": [
            attr(material, short_quote, known["rawValue"], known["name"], attributeKey="architecture")]})
        self.assertEqual(result["metadata"]["retainedAttributes"], 1)
        attribute = result["nodes"][0]["payload"]["attributes"][0]
        ref = attribute["evidenceRefs"][0]
        start = material["body"].rfind("\n", 0, known["evidence"]["startChar"]) + 1
        end = material["body"].find("\n", known["evidence"]["endChar"])
        self.assertEqual((ref["startChar"], ref["endChar"]), (start, end))
        self.assertEqual(ref["quote"], material["body"][start:end])
        self.assertIn("- **The brain (MARS):**", ref["quote"])
        self.assertNotIn("The body (platform)", ref["quote"])
        self.assertIn("MARS Mission System", ref["quote"])
        self.assertIn(short_quote, ref["quote"])
        self.assertIn(known["rawValue"], ref["quote"])
        draft = attribute["draftMetadata"]
        self.assertEqual(draft["quoteRecoveryMethod"], "explicit-acronym-same-paragraph")
        self.assertEqual(draft["originalModelQuote"], short_quote)
        locator = draft["originalQuoteLocator"]
        self.assertEqual(material["body"][locator["startChar"]:locator["endChar"]], short_quote)
        self.assertNotIn("originalModelQuote", ref)
        self.assertEqual(attribute["claimKind"], "manufacturer-claim")
        self.assertEqual(attribute["reviewStatus"], "pending")
        self.assertTrue(draft["humanReviewRequired"])
        self.assertFalse(draft["truthVerified"])
        self.assertFalse(attribute["truthVerified"])
        self.validate_contract("RetainedAttributeDraft", attribute)
        self.validate_contract("PerObjectExtractionMetadata", result["nodes"][0]["payload"]["attributeExtraction"])
        checks = build_object_verification_checks({"assessmentId": "test", "sourceSnapshot": [material]}, {"nodes": result["nodes"], "edges": []})
        self.assertEqual(checks[0]["machineStatus"], "passed")

    def test_acronym_recovery_never_borrows_next_bullet_paragraph_or_heading(self):
        short = "Expanded System (MARS) Mission System is a modular, open architecture solution."
        named = "MARS Mission System is a public technology."
        for separator in ("\n- ", "\n* ", "\n1. ", "\n• ", "\n\n", "\n\n## Other object\n", "\n## Other object\n"):
            with self.subTest(separator=repr(separator)):
                material = source(short + separator + named)
                original = node(material, "MARS Mission System", "Technology", identity_quote=named)
                result = enrich_object_attributes([material], [original], lambda _: {"attributes": [
                    attr(material, short, "a modular, open architecture solution", attributeKey="architecture")]})
                self.assertEqual(result["metadata"]["retainedAttributes"], 0)
                reason = result["nodes"][0]["payload"]["attributeExtraction"]["rejectedProposals"][0]["reason"]
                # An identity in the next paragraph may prevent the short
                # definition from being sent at all, an earlier safe failure.
                self.assertIn(reason, {"unsupported-object-context", "quote-not-in-object-window"})
                full_window = {"excerpts": [{"startChar": 0, "endChar": len(material["body"]), "text": material["body"]}]}
                self.assertIsNone(_recover_explicit_acronym_context(material["body"], full_window, 0, short,
                    "a modular, open architecture solution", "MARS Mission System", material["itemId"], original["payload"]["evidenceRefs"]))

    def test_acronym_recovery_rejects_unknown_or_partial_alias_and_unlabeled_camera(self):
        for label, short in (
            ("MARS Mission System", "Expanded System (MARS) Autonomy is an open interface."),
            ("MARS Mission System", "Expanded System (MARS) Mission Systems is an open interface."),
            ("MARS Mission System", "Expanded System (mars) Mission System is an open interface."),
            ("MARS Mission System", "Expanded System MMS is an open interface."),
            ("Camera X", "Camera Y uses an open interface."),
            ("Camera X", "It uses an open interface."),
        ):
            with self.subTest(label=label, short=short):
                named = label + " is a public technology."
                material = source(short + " " + named)
                original = node(material, label, "Technology", identity_quote=named)
                result = enrich_object_attributes([material], [original], lambda _: {"attributes": [attr(material, short)]})
                self.assertEqual(result["metadata"]["retainedAttributes"], 0)
                self.assertEqual(result["nodes"][0]["payload"]["attributeExtraction"]["rejectedReasonCounts"], {"unsupported-object-context": 1})

    def test_acronym_recovery_requires_one_whole_selected_window_and_bounded_block(self):
        short = "Expanded System (MARS) Mission System uses an open interface."
        named = "MARS Mission System is a public technology."
        material = source(short + " " + named)
        identity_refs = node(material, "MARS Mission System", "Technology", identity_quote=named)["payload"]["evidenceRefs"]
        def selection(ranges):
            return {"excerpts": [{"startChar": start, "endChar": end, "text": material["body"][start:end]} for start, end in ranges]}
        # These individually exact adjacent windows cannot be stitched.
        windows = selection([(0, len(short)), (len(short), len(material["body"]))])
        self.assertIsNone(_recover_explicit_acronym_context(material["body"], windows, 0, short,
            "open interface", "MARS Mission System", material["itemId"], identity_refs))
        # Nor can an otherwise valid quotation widen past a single window end.
        windows = selection([(0, len(material["body"]) - 1)])
        self.assertIsNone(_recover_explicit_acronym_context(material["body"], windows, 0, short,
            "open interface", "MARS Mission System", material["itemId"], identity_refs))
        windows = selection([(0, len(material["body"]))])
        self.assertEqual(_recover_explicit_acronym_context(material["body"], windows, 0, short,
            "open interface", "MARS Mission System", material["itemId"], identity_refs), (0, material["body"]))
        # A long original paragraph is rejected, never truncated to force a
        # name/value into a permissible quotation.
        long_material = source(short + " " + named + " " + "context " * 150)
        original = node(long_material, "MARS Mission System", "Technology", identity_quote=named)
        result = enrich_object_attributes([long_material], [original], lambda _: {"attributes": [attr(long_material, short)]})
        self.assertGreater(len(long_material["body"]), 1200)
        self.assertLess(len(long_material["body"]), 1600)
        self.assertEqual(result["metadata"]["retainedAttributes"], 0)
        self.assertEqual(result["nodes"][0]["payload"]["attributeExtraction"]["rejectedReasonCounts"], {"unsupported-object-context": 1})

    def test_acronym_recovery_rejects_ambiguous_repeat_and_foreign_identity_locator(self):
        short = "Expanded System (MARS) Mission System uses an open interface."
        named = "MARS Mission System is a public technology."
        material = source(short + " " + named + "\n\n" + short + " " + named)
        identity_refs = node(material, "MARS Mission System", "Technology", identity_quote=named)["payload"]["evidenceRefs"]
        windows = {"excerpts": [{"startChar": 0, "endChar": len(material["body"]), "text": material["body"]}]}
        self.assertIsNone(_recover_explicit_acronym_context(material["body"], windows, 0, short,
            "open interface", "MARS Mission System", material["itemId"], identity_refs))
        # A verified identity located in the other paragraph cannot authorize
        # widening the first paragraph's otherwise matching short definition.
        foreign = source(short + " MARS Mission System is a public technology.\n\n" + named)
        identity = node(foreign, "MARS Mission System", "Technology", identity_quote=named)["payload"]["evidenceRefs"][0]
        identity["startChar"] = foreign["body"].rfind(named)
        identity["endChar"] = identity["startChar"] + len(named)
        windows = {"excerpts": [{"startChar": 0, "endChar": len(foreign["body"]), "text": foreign["body"]}]}
        self.assertIsNone(_recover_explicit_acronym_context(foreign["body"], windows, 0, short,
            "open interface", "MARS Mission System", foreign["itemId"], [identity]))

    def test_other_subject_rejection_checks_short_quote_before_expansion(self):
        named = "MARS Mission System uses an open interface."
        short = "Expanded System (MARS) Mission System is documented. Camera Y uses an open interface."
        material = source(named + " " + short)
        objects = [node(material, "MARS Mission System", "Technology", identity_quote=named),
                   node(material, "Camera Y", "Technology", identity_quote="Camera Y uses an open interface.")]
        result = enrich_object_attributes([material], objects, lambda _: {"attributes": [attr(material, short)]}, object_ids=[objects[0]["id"]])
        self.assertEqual(result["metadata"]["retainedAttributes"], 0)
        self.assertEqual(result["nodes"][0]["payload"]["attributeExtraction"]["rejectedReasonCounts"], {"other-object-value-context": 1})

    def test_field_context_rejects_advertising_architecture_and_purchase_misclassification(self):
        for quote, value, key in (
            ("MARS Mission System accelerates the OODA loop in order to outpace adversaries.", "accelerates the OODA loop", "human-oversight"),
            ("MARS Mission System retains human supervision. It accelerates the OODA loop.", "accelerates the OODA loop", "human-oversight"),
            ("Camera X has an open architecture.", "open architecture", "interface-standard"),
            ("Camera X was purchased following a procurement evaluation.", "purchased", "public-test-status"),
            ("Camera X was tested in a laboratory. It was purchased by a public agency.", "purchased", "public-test-status"),
            ("Camera X has a published interface. It uses a modular architecture.", "modular architecture", "interface-standard"),
        ):
            with self.subTest(key=key):
                material = source(quote)
                original = node(material, "MARS Mission System" if key == "human-oversight" else "Camera X", "Technology")
                result = enrich_object_attributes([material], [original], lambda _: {"attributes": [attr(material, quote, value, attributeKey=key)]})
                self.assertEqual(result["metadata"]["retainedAttributes"], 0)
                self.assertEqual(result["nodes"][0]["payload"]["attributeExtraction"]["rejectedReasonCounts"], {"unsupported-field-context": 1})
                self.assertTrue(all(field["state"] == "not-extracted" for field in result["nodes"][0]["payload"]["attributeExtraction"]["coverage"]["fieldStates"]))

    def test_field_context_allows_literal_cues_without_marking_truth_or_reclassifying(self):
        for quote, value, key in (
            ("Camera X retains human supervision.", "human supervision", "human-oversight"),
            ("Camera X uses a published interface protocol.", "interface protocol", "interface-standard"),
            ("Camera X was demonstrated during a laboratory test.", "demonstrated", "public-test-status"),
            ("Camera X is a modular open architecture solution.", "modular open architecture solution", "architecture"),
        ):
            with self.subTest(key=key):
                material = source(quote)
                original = node(material, kind="Technology")
                result = enrich_object_attributes([material], [original], lambda _: {"attributes": [attr(material, quote, value, attributeKey=key)]})
                self.assertEqual(result["metadata"]["retainedAttributes"], 1)
                attribute = result["nodes"][0]["payload"]["attributes"][0]
                self.assertEqual(attribute["attributeKey"], key)
                self.assertEqual(attribute["state"], "pending-review")
                self.assertFalse(attribute["truthVerified"])
                self.assertNotIn("draftMetadata", attribute)
                self.validate_contract("RetainedAttributeDraft", attribute)

    def test_merged_recovered_quotes_keep_original_model_audit_for_each_source(self):
        short = "Expanded System (MARS) Mission System uses an open interface."
        named = "MARS Mission System is a public technology."
        materials = [source(short + " " + named), source(short + " " + named, "source-b")]
        original = node(materials[0], "MARS Mission System", "Technology", identity_quote=named)
        original["payload"]["evidenceRefs"].extend(node(materials[1], "MARS Mission System", "Technology", identity_quote=named)["payload"]["evidenceRefs"])
        original["evidenceIds"].append(materials[1]["itemId"])
        result = enrich_object_attributes(materials, [original], lambda _: {"attributes": [attr(material, short) for material in materials]})
        self.assertEqual(result["metadata"]["retainedAttributes"], 1)
        self.assertEqual(result["metadata"]["mergedEvidenceRefCount"], 1)
        attribute = result["nodes"][0]["payload"]["attributes"][0]
        self.assertEqual(attribute["draftMetadata"]["originalQuoteLocator"]["itemId"], "source-a")
        self.assertEqual(attribute["draftMetadata"]["additionalQuoteRecoveries"][0]["originalQuoteLocator"]["itemId"], "source-b")
        self.assertFalse(attribute["truthVerified"])
        self.validate_contract("RetainedAttributeDraft", attribute)

    def test_model_review_recommendation_is_bound_to_attribute_without_accepting(self):
        material = source("Camera X uses an open interface.")
        proposal = attr(material, material["body"], reviewRecommendation={"action": "more-evidence", "reason": "还需独立出处确认接口描述。"})
        result = enrich_object_attributes([material], [node(material)], lambda _: {"attributes": [proposal]})
        attribute = result["nodes"][0]["payload"]["attributes"][0]
        recommendation = attribute["reviewRecommendation"]
        self.assertEqual(recommendation["action"], "more-evidence")
        self.assertEqual(recommendation["source"], "model")
        self.assertEqual(recommendation["evidenceRefs"], attribute["evidenceRefs"])
        self.assertTrue(recommendation["humanReviewRequired"])
        self.assertFalse(recommendation["truthVerified"])
        self.assertEqual(attribute["state"], "pending-review")
        proposal["reviewRecommendation"] = {"action": "accept", "reason": "自动接受"}
        invalid = enrich_object_attributes([material], [node(material)], lambda _: {"attributes": [proposal]})
        self.assertNotIn("reviewRecommendation", invalid["nodes"][0]["payload"]["attributes"][0])

    def test_event_property_requires_same_source_overlap(self):
        identity = "Camera X was tested with human oversight on 12 June 2026."
        later = "A later demonstration used an open interface."
        material = source(identity + "\n\n" + later)
        external = source(identity, "other")
        event = node(material, "公开受控试验", "EventRecord", identity_quote=identity, eventKind="TestEvent")
        result = enrich_object_attributes([material, external], [event], lambda _: {"attributes": [
            attr(material, identity, "human oversight"), attr(material, later), attr(external, identity, "human oversight")]})
        self.assertEqual(result["metadata"]["retainedAttributes"], 1)
        self.assertEqual(result["metadata"]["rejectedAttributes"], 2)
        self.assertEqual(result["nodes"][0]["payload"]["attributes"][0]["claimKind"], "public-reported-test")

    def test_identity_provenance_mismatch_causes_no_call(self):
        material = source("Camera X uses an open interface.")
        original = node(material)
        original["payload"]["evidenceRefs"][0]["sourceVersion"] = "wrong-version"
        calls = []
        result = enrich_object_attributes([material], [original], lambda messages: calls.append(messages))
        self.assertEqual(calls, [])
        self.assertEqual(result["nodes"][0]["payload"]["attributeExtraction"]["status"], "invalid-identity-evidence")

    def test_no_callback_does_not_fake_tool_trace(self):
        material = source("Camera X is a platform.")
        events = []
        with execution_context(events.append, task_id="task"):
            result = enrich_object_attributes([material], [node(material)])
        self.assertEqual(events, [])
        self.assertEqual(result["metadata"]["calls"], 0)
        self.assertEqual(result["nodes"][0]["payload"]["attributeExtraction"]["status"], "not-configured")

    def test_real_callback_trace_has_target_and_final_counts_without_source_text(self):
        material = source("Camera X uses an open interface.")
        original = node(material)
        events = []
        with execution_context(events.append, task_id="task"):
            enrich_object_attributes([material], [original], lambda _: {"attributes": [attr(material, material["body"])]}, model="test-model")
        self.assertEqual([event["status"] for event in events], ["started", "succeeded"])
        self.assertEqual(events[-1]["details"]["objectId"], original["id"])
        self.assertEqual(events[-1]["details"]["retainedAttributeCount"], 1)
        self.assertEqual(events[-1]["operation"], "extract-object-attributes")
        self.assertNotIn(material["body"], json.dumps(events))

    def test_returned_wrong_object_id_records_failed_trace_and_preserves_identity(self):
        material = source("Camera X uses an open interface.")
        original = node(material)
        events = []
        with execution_context(events.append, task_id="task"):
            result = enrich_object_attributes([material], [original], lambda _: {"objectId": "other-object", "attributes": []})
        self.assertEqual([event["status"] for event in events], ["started", "failed"])
        self.assertEqual(result["nodes"][0]["id"], original["id"])
        self.assertEqual(result["nodes"][0]["payload"]["attributeExtraction"]["status"], "failed")

    def test_checkpoint_callback_receives_detached_final_node_and_stops_on_storage_failure(self):
        material = source("Camera X is a platform. Camera Y is a platform.")
        objects = [node(material, "Camera X"), node(material, "Camera Y")]
        calls, checkpoints = [], []
        def request(messages):
            calls.append(messages)
            return {"attributes": []}
        def checkpoint(enriched, row):
            checkpoints.append(row["status"])
            self.assertEqual(enriched["payload"]["attributeExtraction"]["status"], row["status"])
            row["status"] = "mutated-callback-copy"
            raise RuntimeError("checkpoint-failed")
        with self.assertRaisesRegex(RuntimeError, "checkpoint-failed"):
            enrich_object_attributes([material], objects, request, on_object=checkpoint)
        self.assertEqual(len(calls), 1)
        self.assertEqual(checkpoints, ["no-explicit-attributes"])

    def test_literal_value_qualifier_and_inference_are_strictly_rejected(self):
        material = source("Camera X reported 40 kg in a laboratory test.")
        result = enrich_object_attributes([material], [node(material)], lambda _: {"attributes": [
            attr(material, material["body"], 4), attr(material, material["body"], "40", unit="lbs"),
            attr(material, material["body"], "40", conditions="all environments"),
            attr(material, material["body"], "40", claimKind="analysis-inference"),
            attr(material, material["body"], "unknown"), attr(material, material["body"], True)]})
        self.assertEqual(result["metadata"]["retainedAttributes"], 0)
        self.assertEqual(result["metadata"]["rejectedAttributes"], 6)


if __name__ == "__main__":
    unittest.main()
