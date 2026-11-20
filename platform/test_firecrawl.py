"""Search boundary tests; no API keys or live network calls."""
import hashlib
import io
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

try:
    from .osint_collector import FirecrawlSearchProvider, OSINTCollector, OSINTCollectionError, _NoSearchRedirect
except ImportError:
    from osint_collector import FirecrawlSearchProvider, OSINTCollector, OSINTCollectionError, _NoSearchRedirect


class FirecrawlTests(unittest.TestCase):
    def provider(self, rows):
        opener = Mock()
        opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(json.dumps({"success": True, "data": rows}).encode())
        return FirecrawlSearchProvider(api_key="fixture-token", opener=opener), opener

    def test_time_window_source_url_hash_and_truncation(self):
        provider, opener = self.provider([
            {"title": "Public contract", "url": "https://defense.gov/contract", "markdown": "Evidence\n" * 4000},
            {"title": "Unsafe", "url": "https://127.0.0.1/a", "markdown": "Skip"},
            {"title": "Old", "url": "https://defense.gov/old", "markdown": "Old", "publishedAt": "2020-01-01"},
        ])
        result = provider.search("equipment procurement", [], since_days=90, max_items=3, source_ids=["defense-official"], now=datetime(2026,9,29,tzinfo=timezone.utc))
        request = opener.open.call_args.args[0]
        self.assertIn("after:2026-07-01", json.loads(request.data)["query"])
        self.assertNotIn("fixture-token", json.dumps(result))
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertTrue(item["provenance"]["truncated"])
        self.assertFalse(item["provenance"]["publicationDateVerified"])
        self.assertEqual(item["sourceName"], "defense.gov")
        self.assertEqual(item["contentHash"], hashlib.sha256(item["body"].encode()).hexdigest())

    def test_v2_web_shape_is_discovered_then_scraped(self):
        opener = Mock()
        def respond(request, **kwargs):
            if request.full_url.endswith("/search"):
                return io.BytesIO(json.dumps({"success": True, "data": {"web": [{"title":"Brief", "url":"https://defense.gov/brief", "description":"Search snippet"}]}}).encode())
            return io.BytesIO(json.dumps({"success": True, "data": {"markdown": "Collected public body"}}).encode())
        opener.open.side_effect = respond
        provider = FirecrawlSearchProvider(api_key="fixture-token", opener=opener)
        item=provider.search("procurement",[],since_days=0,max_items=1,source_ids=["defense-official"])["items"][0]
        self.assertEqual(item["provenance"]["contentKind"],"markdown")
        self.assertEqual(opener.open.call_count, 2)

    def test_metadata_search_then_scrape_collects_bounded_body(self):
        opener = Mock()
        calls = []
        def respond(request, **kwargs):
            payload = json.loads(request.data)
            calls.append((request.full_url, payload))
            if request.full_url.endswith("/search"):
                return io.BytesIO(json.dumps({"success": True, "data": [{"title": "UAV interoperability trial", "url": "https://defense.gov/uav-trial", "description": "UAV public trial"}]}).encode())
            return io.BytesIO(json.dumps({"success": True, "data": {"markdown": "UAS and UAV interoperability evidence.", "metadata": {"publishedAt": "2026-09-01"}}}).encode())
        opener.open.side_effect = respond
        provider = FirecrawlSearchProvider(api_key="fixture-token", opener=opener)
        result = provider.search("UAV collaboration", ['(UAS OR UAV OR "uncrewed aircraft")'], since_days=180, max_items=20, source_ids=["defense-official"], now=datetime(2026,10,9,tzinfo=timezone.utc),ensure_target=True)
        self.assertEqual(calls[0][0].rsplit('/',1)[-1],'search')
        self.assertEqual(sum(url.endswith('/scrape') for url,_ in calls),1)
        # ``searchRounds`` counts bounded loop iterations. A later round may be
        # skipped when its normalized scope/query pair was already paid for;
        # executedRequests and queryPlan therefore describe actual provider
        # calls, while actualRounds describes loop iterations.
        actual_plans = [plan for plan in result["queryPlan"] if plan["status"] != "skipped-duplicate-query"]
        self.assertEqual(sum(url.endswith('/search') for url,_ in calls), result['budget']['executedRequests'])
        self.assertEqual(len(actual_plans), result['budget']['executedRequests'])
        self.assertEqual(result['budget']['plannedRequests'], result['budget']['executedRequests'])
        self.assertLessEqual(result['budget']['actualRounds'], result['budget']['maxCollectionRounds'])
        self.assertNotIn("scrapeOptions", calls[0][1])
        self.assertEqual(calls[1][1]["url"], "https://defense.gov/uav-trial")
        self.assertEqual(result["queryPlan"][0]["targetAnchors"], ["UAS", "UAV", "uncrewed aircraft"])
        self.assertEqual(result["sourceResults"][0]["scrapeRequestCount"], 1)
        self.assertEqual(len(result['items']),1)
        self.assertEqual(result['budget']['stopReason'],'bounded-rounds-exhausted')
        self.assertEqual(result["items"][0]["body"], "UAS and UAV interoperability evidence.")

    def test_oversized_search_retries_with_smaller_limit(self):
        opener = Mock()
        limits = []
        oversized = b"x" * 4_000_001
        def respond(request, **kwargs):
            payload = json.loads(request.data)
            limits.append(payload["limit"])
            if len(limits) < 3:
                return io.BytesIO(oversized)
            return io.BytesIO(json.dumps({"success": True, "data": []}).encode())
        opener.open.side_effect = respond
        provider = FirecrawlSearchProvider(api_key="fixture-token", opener=opener)
        result = provider.search("equipment", [], since_days=0, max_items=20, source_ids=["defense-official"])
        self.assertEqual(limits[:3], [20, 10, 5])
        self.assertEqual(len(limits),result['budget']['searchRounds']+result['budget']['retryRequests'])
        self.assertTrue(all(limit<=20 for limit in limits))
        self.assertEqual(result["sourceResults"][0]["searchRetryCount"], 2)
        self.assertEqual(result["budget"]["retryRequests"], 2)

    def test_key_error_and_redirect_do_not_switch_to_rss(self):
        with self.assertRaises(OSINTCollectionError):
            FirecrawlSearchProvider(api_key="").search("procurement",[],since_days=30,max_items=1)
        provider, opener = self.provider([])
        opener.open.side_effect = HTTPError("https://api.firecrawl.dev/v1/search",401,"rejected",{},None)
        result = provider.search("procurement",[],since_days=30,max_items=1)
        self.assertIn("Firecrawl HTTP 401", result["errors"][0]["error"])
        with self.assertRaises(OSINTCollectionError):
            _NoSearchRedirect().redirect_request(None,None,302,"",{},"https://elsewhere.example")

    def test_transport_timeout_reports_actual_timeout_without_rss_fallback(self):
        provider, opener = self.provider([])
        opener.open.side_effect = TimeoutError("fixture timeout")
        result = provider.search("procurement", [], since_days=30, max_items=1, source_ids=["defense-official"])
        self.assertIn("timed out after 90s", result["errors"][0]["error"])
        self.assertEqual(result["collector"], "firecrawl-search")

        opener.open.side_effect = URLError(TimeoutError("fixture timeout"))
        result = provider.search("procurement", [], since_days=30, max_items=1, source_ids=["defense-official"])
        self.assertIn("timed out after 90s", result["errors"][0]["error"])

    def test_selected_search_never_fetches_rss(self):
        provider, _=self.provider([])
        with patch.dict("os.environ",{"CRK_SEARCH_PROVIDER":""}):
            collector=OSINTCollector(search_provider=provider)
        with patch.object(collector,"_fetch",side_effect=AssertionError("RSS must not run")):
            result=collector.collect(["firecrawl-search"],[],question="equipment procurement",since_days=30,max_items=1)
        self.assertEqual(result["sourceResults"][0]["sourceId"],"defense-official")

    def test_chinese_query_retains_mandatory_site_scope_and_filters_spoofed_hosts(self):
        provider, opener = self.provider([
            {"url":"https://www.gao.gov/report", "markdown":"Public equipment test"},
            {"url":"https://gao.gov.attacker.example/report", "markdown":"Spoofed"},
            {"url":"https://evilgao.gov/report", "markdown":"Suffix spoofed"},
            {"url":"https://example.org/?url=https://gao.gov", "markdown":"Parameter spoofed"},
            {"url":"https://defensenews.com/report", "markdown":"Unselected media"},
        ])
        result = provider.search("未来装备技术发展趋势", ["equipment", "technology"], since_days=30, max_items=5, source_ids=["test-evaluation"])
        queries=[json.loads(call.args[0].data)['query'] for call in opener.open.call_args_list]
        query = queries[0]
        self.assertIn("site:gao.gov", query)
        self.assertIn("equipment technology", query)
        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(all('site:gao.gov' in query for query in queries))
        self.assertGreaterEqual(result["sourceResults"][0]["excludedCounts"]["outsideScope"], 4)
        self.assertLessEqual(result["sourceResults"][0]["excludedCounts"]["outsideScope"], 4*result['budget']['searchRounds'])
        self.assertEqual(result["items"][0]["provenance"]["verificationStatus"], "unverified")

    def test_group_budget_is_global_and_partial_failures_remain_visible(self):
        provider, opener = self.provider([])
        calls=[]
        def respond(request, **kwargs):
            payload=json.loads(request.data);calls.append(payload)
            if "site:gao.gov" in payload["query"]:
                raise HTTPError("https://api.firecrawl.dev/v1/search",503,"unavailable",{},None)
            return io.BytesIO(json.dumps({"data":[{"url":"https://defense.gov/contract", "markdown":"Public contract record"}]}).encode())
        opener.open.side_effect=respond
        result=provider.search("military equipment",[],since_days=30,max_items=8,source_ids=["procurement-budget","test-evaluation","defense-official"])
        self.assertTrue(all(1<=call['limit']<=3 for call in calls))
        self.assertLessEqual(result['budget']['executedRequests'],result['budget']['maxRequests'])
        self.assertEqual(result['budget']['requestedLimitTotal'],sum(plan['requestedLimit'] for plan in result['queryPlan']))
        self.assertEqual(len(result["items"]),1)
        self.assertEqual(result["budget"]["plannedRequests"],3*result['budget']['searchRounds'])
        self.assertEqual(len(result["errors"]),result['budget']['searchRounds'])
        self.assertEqual(result["items"][0]["provenance"]["retrievedInScopes"],["defense-official","procurement-budget"])

    def test_manufacturer_is_a_claim_and_no_domain_escape_or_date_verification(self):
        provider, _=self.provider([{"url":"https://www.rtx.com/news", "markdown":"Vendor product claim", "publishedAt":"2026-09-01"}])
        item=provider.search("equipment",[],since_days=0,max_items=1,source_ids=["manufacturer-disclosure"])["items"][0]
        self.assertEqual(item["contentNature"],"manufacturer-claim")
        self.assertEqual(item["sourceRole"],"manufacturer-disclosure")
        self.assertFalse(item["provenance"]["publicationDateVerified"])
        self.assertTrue(item["provenance"]["publicationDatePresent"])
        self.assertFalse(item["documentTypeVerified"])

    def test_legacy_scope_and_tiny_target_use_bounded_rounds(self):
        provider, opener=self.provider([])
        result=provider.search("equipment",[],since_days=30,max_items=1,source_ids=["firecrawl-search"])
        self.assertEqual(opener.open.call_count,3*result['budget']['searchRounds'])
        self.assertLessEqual(opener.open.call_count,result['budget']['maxRequests'])
        self.assertEqual(result['budget']['stopReason'],'bounded-rounds-exhausted')
        self.assertEqual(len(result['items']),0)
        with self.assertRaises(OSINTCollectionError):
            provider.search("equipment",[],since_days=30,max_items=20,source_ids=["made-up"])


if __name__ == "__main__":
    unittest.main()
