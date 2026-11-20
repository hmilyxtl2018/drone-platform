"""Subject-first and image provenance boundary tests; no live requests."""
import io
import hashlib
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

try:
    from .osint_collector import FirecrawlSearchProvider
    from .osint_sources import DEFAULT_SCOPE_IDS, SEARCH_SCOPE_BY_ID
except ImportError:
    from osint_collector import FirecrawlSearchProvider
    from osint_sources import DEFAULT_SCOPE_IDS, SEARCH_SCOPE_BY_ID


class DirectedCollectionTests(unittest.TestCase):
    def provider(self, data, *, base_url=None):
        opener = Mock()
        opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(json.dumps({"success": True, "data": data}).encode())
        return FirecrawlSearchProvider(api_key="fixture-token", opener=opener, base_url=base_url), opener

    def search(self, provider, **kwargs):
        return provider.search("公开平台的互操作试验趋势", ['("Primoco" OR "MARS Autonomy")', '(interoperability OR "human supervision")'], since_days=0, max_items=8, source_ids=["manufacturer-disclosure"], **kwargs)

    def test_named_subject_gate_cannot_be_satisfied_by_context_quote(self):
        provider, _ = self.provider([
            {"url": "https://airbus.com/generic", "title": "Industry overview", "markdown": "Human supervision and interoperability trends."},
            {"url": "https://airbus.com/primoco", "title": "Primoco evaluation", "markdown": "Primoco supports an open interface in this reported demonstration."},
            {"url": "https://airbus.com/mars", "title": "MARS Autonomy", "markdown": "MARS Autonomy interface disclosure."},
            {"url": "https://airbus.com/spoof", "title": "Not the named product", "markdown": "Primocology is a fictional string."},
        ])
        result = self.search(provider)
        self.assertEqual([x["itemUri"] for x in result["items"]], ["https://airbus.com/primoco", "https://airbus.com/mars"])
        self.assertEqual(result["sourceResults"][0]["excludedCounts"]["unrelatedSubject"], 2)
        self.assertEqual(result["items"][0]["provenance"]["matchedTargetAnchors"], ["Primoco"])
        self.assertEqual(result["queryPlan"][0]["targetAnchors"], ["Primoco", "MARS Autonomy"])
        self.assertNotIn("fixture-token", json.dumps(result))

    def test_generic_annual_requires_subject_in_saved_body_not_search_title(self):
        provider, _ = self.provider([
            {"url": "https://airbus.com/annual", "title": "Primoco annual report", "markdown": "Table of Contents\n1. Introduction\n2. Financial report"},
            {"url": "https://airbus.com/annual-grounded", "title": "Annual report", "markdown": "Table of Contents\nPrimoco interface evaluation is reported in this section."},
        ])
        result = self.search(provider)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["sourceResults"][0]["excludedCounts"]["genericSummary"], 1)

    def test_unquoted_legacy_keywords_keep_previous_collection_behavior(self):
        provider, _ = self.provider([{"url": "https://airbus.com/report", "title": "Annual report", "markdown": "Table of Contents\nIndustry reporting"}])
        result = provider.search("technology", ["equipment"], since_days=0, max_items=1, source_ids=["manufacturer-disclosure"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["sourceResults"][0]["relevanceGate"], "legacy-keywords")

    def test_actual_linked_images_are_metadata_not_satellite_measurements(self):
        markdown = "Primoco public evaluation.\n![Factory photograph](https://airbus.com/media/factory.jpg)\n![Logo](https://airbus.com/media/site-logo.png)\n![Unsafe](http://airbus.com/media/plain.jpg)\n<img src='/images/overview.jpg' alt='Public overview'>\n![same](https://airbus.com/media/factory.jpg)"
        provider, opener = self.provider([{"url": "https://airbus.com/reports/evaluation", "title": "Primoco", "markdown": markdown}])
        item = self.search(provider)["items"][0]
        refs = item["provenance"]["mediaReferences"]
        self.assertEqual([x["imageUrl"] for x in refs], ["https://airbus.com/media/factory.jpg", "https://airbus.com/images/overview.jpg"])
        self.assertTrue(all(x["mediaKind"] == "unclassified-image" and x["captureDate"] is None and x["verificationStatus"] == "unverified" for x in refs))
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(item["body"], markdown)

    def test_image_scope_is_optional_and_does_not_replace_official_defaults(self):
        try:
            from .imagery_evidence import PUBLIC_IMAGERY_ROOTS
        except ImportError:
            from imagery_evidence import PUBLIC_IMAGERY_ROOTS
        self.assertNotIn("public-imagery", DEFAULT_SCOPE_IDS)
        scope = SEARCH_SCOPE_BY_ID["public-imagery"]
        self.assertEqual(tuple(scope["domains"]), PUBLIC_IMAGERY_ROOTS)
        self.assertEqual(scope["sourceReliability"], "unknown")
        self.assertEqual(scope["searchType"], "images")

    def test_public_commercial_archive_results_remain_unverified_references(self):
        publishers = ["planet.com", "maxar.com", "vantor.com", "airbus.com", "intelligence-airbusds.com"]
        rows = [{"url":f"https://www.{host}/public-archive", "imageUrl":f"https://www.{host}/archive-image.jpg", "title":"Primoco archived satellite imagery"} for host in publishers]
        rows.append({"url":"https://planet.com.attacker.example/archive", "imageUrl":"https://planet.com/image.jpg", "title":"Primoco satellite image"})
        provider, opener = self.provider({"images":rows})
        result=provider.search("公开历史背景", ['"Primoco"'], since_days=0, max_items=6, source_ids=["public-imagery"])
        self.assertEqual(len(result["items"]),5)
        self.assertEqual(result["sourceResults"][0]["excludedCounts"]["outsideScope"],1)
        self.assertTrue(all(item["body"]=="" and item["provenance"]["verificationStatus"]=="unverified" and item["provenance"]["mediaReferences"][0]["mediaKind"]=="unclassified-image" for item in result["items"]))
        payload=json.loads(opener.open.call_args.args[0].data)
        self.assertIn('"Primoco"',payload["query"])
        self.assertIn('"satellite imagery"',payload["query"])
        self.assertTrue(all(f"site:{host}" in payload["query"] for host in publishers))
        self.assertEqual(opener.open.call_count,1)

    def test_v2_image_api_records_only_linked_metadata_no_body_or_pixels(self):
        provider, opener = self.provider({"images": [{"url": "https://www.esa.int/archive/public-image", "imageUrl": "https://www.esa.int/images/public.jpg", "title": "Primoco archived imagery"}]})
        result = provider.search("公开平台背景", ['"Primoco"'], since_days=90, max_items=2, source_ids=["public-imagery"], now=datetime(2026, 10, 8, tzinfo=timezone.utc))
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.firecrawl.dev/v2/search")
        self.assertEqual(payload["sources"], ["images"])
        self.assertNotIn("scrapeOptions", payload)
        self.assertIn("site:esa.int", payload["query"])
        self.assertNotIn("acquisition date", payload["query"])
        self.assertNotIn(" archive", payload["query"])
        self.assertIn("after:2026-07-10", payload["query"])
        item = result["items"][0]
        self.assertEqual(item["body"], "")
        self.assertEqual(item["reviewState"], "pending-review")
        self.assertEqual(item["provenance"]["retrievalKind"], "search-discovery-only")
        self.assertEqual(item["provenance"]["contentKind"], "image-search-metadata")
        self.assertFalse(item["provenance"]["publicationDatePresent"])
        self.assertEqual(item["provenance"]["mediaReferences"][0]["captureDate"], None)
        self.assertNotIn("confidence", item)
        self.assertEqual(opener.open.call_count, 1)

    def test_image_query_keeps_subject_without_web_capability_context(self):
        subject='("Primoco" OR "MARS Autonomy")'
        context='(interoperability OR "open architecture" OR demonstration)'
        terms=[subject,context]
        provider,_=self.provider([])
        image_query=provider._query("Primoco interoperability trend",terms,scope=SEARCH_SCOPE_BY_ID["public-imagery"],since_days=30,now=datetime(2026,10,8,tzinfo=timezone.utc))
        web_query=provider._query("Primoco interoperability trend",terms,scope=SEARCH_SCOPE_BY_ID["manufacturer-disclosure"],since_days=30,now=datetime(2026,10,8,tzinfo=timezone.utc))
        self.assertIn('"Primoco"',image_query)
        self.assertIn('"MARS Autonomy"',image_query)
        self.assertIn('"satellite image"',image_query)
        self.assertIn("site:planet.com",image_query)
        self.assertIn("after:2026-09-08",image_query)
        self.assertNotIn("interoperability",image_query)
        self.assertNotIn("open architecture",image_query)
        self.assertNotIn("demonstration",image_query)
        self.assertNotIn("acquisition date",image_query)
        self.assertIn(context,web_query)
        self.assertIn(subject,web_query)

    def test_unquoted_legacy_image_query_preserves_topic_keywords(self):
        provider,_=self.provider({"images":[]})
        result=provider.search("公开无人机资料",["civil UAV", "Primoco"],since_days=0,max_items=1,source_ids=["public-imagery"])
        query=result["queryPlan"][0]["query"]
        self.assertIn("civil UAV Primoco",query)
        self.assertIn('"satellite image"',query)
        self.assertEqual(result["queryPlan"][0]["relevanceGate"],"legacy-keywords")

    def test_image_result_gates_source_host_image_url_and_named_subject(self):
        provider, _ = self.provider({"images": [
            {"url": "https://nasa.gov.attacker.example/page", "imageUrl": "https://nasa.gov/image.jpg", "title": "Primoco"},
            {"url": "https://www.nasa.gov/page", "imageUrl": "https://127.0.0.1/image.jpg", "title": "Primoco"},
            {"url": "https://www.nasa.gov/other", "imageUrl": "https://nasa.gov/other.jpg", "title": "Unrelated landscape"},
            {"url": "https://www.nasa.gov/auth", "imageUrl": "https://key:secret@nasa.gov/auth.jpg", "title": "Primoco"},
            {"url": "https://www.nasa.gov/local", "imageUrl": "https://local.dev.local/a.jpg", "title": "Primoco"},
        ]})
        result = provider.search("背景", ['"Primoco"'], since_days=0, max_items=5, source_ids=["public-imagery"])
        self.assertEqual(result["items"], [])
        counts = result["sourceResults"][0]["excludedCounts"]
        self.assertEqual(counts["outsideScope"], 1)
        self.assertEqual(counts["invalidImageUrl"], 3)
        self.assertEqual(counts["unrelatedSubject"], 1)

    def test_same_archive_page_retains_multiple_images_as_one_source(self):
        provider, _ = self.provider({"images": [
            {"url": "https://www.esa.int/public", "imageUrl": "https://www.esa.int/one.jpg", "title": "Primoco one"},
            {"url": "https://www.esa.int/public", "imageUrl": "https://www.esa.int/two.jpg", "title": "Primoco two"},
        ]})
        result = provider.search("背景", ['"Primoco"'], since_days=0, max_items=2, source_ids=["public-imagery"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(len(result["items"][0]["provenance"]["mediaReferences"]), 2)
        self.assertEqual(result["sourceResults"][0]["duplicateCount"], 1)
        item = result["items"][0]
        metadata = json.dumps({"title": item["title"], "sourceUrl": item["itemUri"], "mediaReferences": item["provenance"]["mediaReferences"]}, sort_keys=True, ensure_ascii=False)
        self.assertEqual(item["contentHash"], hashlib.sha256(metadata.encode()).hexdigest())

    def test_missing_v2_image_shape_is_error_empty_image_array_is_valid(self):
        for data in [{"web": []}, []]:
            provider, _ = self.provider(data)
            result = provider.search("背景", [], since_days=0, max_items=1, source_ids=["public-imagery"])
            self.assertEqual(len(result["errors"]), 1)
        provider, _ = self.provider({"images": []})
        result = provider.search("背景", [], since_days=0, max_items=1, source_ids=["public-imagery"])
        self.assertEqual(result["errors"], [])

    def test_custom_endpoint_is_not_silently_rewritten_for_images(self):
        provider, opener = self.provider({"images": []}, base_url="https://custom.example/v1")
        result = provider.search("背景", [], since_days=0, max_items=1, source_ids=["public-imagery"])
        self.assertEqual(len(result["errors"]), 1)
        opener.open.assert_not_called()
        provider, opener = self.provider({"images": []}, base_url="https://api.firecrawl.dev/v2")
        result = provider.search("背景", [], since_days=0, max_items=1, source_ids=["public-imagery"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(opener.open.call_args.args[0].full_url, "https://api.firecrawl.dev/v2/search")

    def test_six_scopes_share_one_budget_with_no_retries(self):
        provider, opener = self.provider([])
        payloads = []
        def respond(request, **kwargs):
            payload = json.loads(request.data)
            payloads.append((request.full_url, payload))
            return io.BytesIO(json.dumps({"success": True, "data": {"images": []} if payload.get("sources") == ["images"] else []}).encode())
        opener.open.side_effect = respond
        scopes = ["defense-official", "test-evaluation", "procurement-budget", "manufacturer-disclosure", "defense-media", "public-imagery"]
        result = provider.search("background", [], since_days=0, max_items=17, source_ids=scopes)
        self.assertEqual(len(payloads), 6)
        self.assertTrue(all(1<=payload['limit']<=3 for _,payload in payloads))
        self.assertEqual(result['budget']['requestedLimitTotal'],sum(payload['limit'] for _,payload in payloads))
        self.assertTrue(all(url.endswith("/v1/search") for url, _ in payloads[:5]))
        self.assertTrue(payloads[-1][0].endswith("/v2/search"))
        self.assertEqual(result["budget"]["maxRequests"], 6)
        self.assertEqual(result["budget"]["retryRequests"], 0)
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
