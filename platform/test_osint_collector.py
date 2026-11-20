"""Deterministic tests for the bounded public-feed OSINT collector."""

from __future__ import annotations

import io
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

try:
    import osint_collector as collector_module
    from osint_collector import FirecrawlSearchProvider, OSINTCollectionError, OSINTCollector, _automated_round_budget, strip_html
except ModuleNotFoundError:  # pragma: no cover
    from . import osint_collector as collector_module
    from .osint_collector import FirecrawlSearchProvider, OSINTCollectionError, OSINTCollector, _automated_round_budget, strip_html


FEED = b'''<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Public Defence Feed</title>
    <lastBuildDate>Tue, 29 Sep 2026 08:00:00 GMT</lastBuildDate>
    <item>
      <title>Budget update: procurement review</title>
      <guid isPermaLink="false">item-001</guid>
      <link>https://public.example/item-001</link>
      <pubDate>Tue, 29 Sep 2026 07:00:00 GMT</pubDate>
      <description><![CDATA[<p>Public &amp; reviewed <strong>budget</strong>.</p><script>drop me</script>]]></description>
    </item>
    <item>
      <title>Old item</title>
      <guid>item-old</guid>
      <pubDate>Tue, 01 Jan 2019 07:00:00 GMT</pubDate>
      <description>Old body</description>
    </item>
  </channel>
</rss>'''

ATOM_FEED = b'''<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Research</title>
  <updated>2026-09-29T08:00:00Z</updated>
  <entry>
    <title>Technology transfer report</title>
    <id>tag:public.example,2026:tech-1</id>
    <link rel="alternate" href="https://public.example/tech-1"/>
    <published>2026-09-29T07:00:00Z</published>
    <summary type="html">&lt;p&gt;Open &lt;b&gt;technology&lt;/b&gt; evidence&lt;/p&gt;</summary>
  </entry>
</feed>'''


class FakeResponse:
    def __init__(self, body: bytes, url: str = "https://feeds.example.test/feed.xml"):
        self.body = body
        self.url = url
        self.offset = 0
        self.headers = {"Content-Length": str(len(body)), "Content-Type": "application/rss+xml"}

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = len(self.body)
        result = self.body[self.offset : self.offset + amount]
        self.offset += len(result)
        return result

    def geturl(self) -> str:
        return self.url

    def close(self) -> None:
        return None


def fixed_dns(host: str, port: int, *args, **kwargs):
    # The network is mocked; use a stable public address for URL validation.
    return [(None, None, None, None, ("8.8.8.8", port))]


class OSINTCollectorTests(unittest.TestCase):
    def _collector(self, body: bytes = FEED, *, max_bytes: int = 2_000_000) -> OSINTCollector:
        source = {"id": "fixture", "name": "Fixture public feed", "url": "https://feeds.example.test/feed.xml", "category": "security-research"}
        return OSINTCollector(
            [source],
            opener=lambda request, timeout: FakeResponse(body, request.full_url),
            max_bytes=max_bytes,
            now=lambda: datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc),
        )

    def test_rss_normalization_filtering_and_evidence_hash(self):
        with patch.object(collector_module.socket, "getaddrinfo", fixed_dns):
            result = self._collector().collect(["fixture"], ["budget"], since_days=30, max_items=20)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["sourceResults"][0]["status"], "ok")
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["sourceId"], "fixture")
        self.assertEqual(item["sourceVersion"], "item-001")
        self.assertEqual(item["matchedKeywords"], ["budget"])
        self.assertEqual(item["itemUri"], "https://public.example/item-001")
        self.assertIn("Public & reviewed budget.", item["body"])
        self.assertNotIn("drop me", item["body"])
        self.assertEqual(len(item["contentHash"]), 64)

    def test_unknown_source_is_visible_and_no_fixture_data_is_invented(self):
        result = self._collector().collect(["missing-source"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["sourceResults"], [])
        self.assertEqual(result["errors"][0]["sourceId"], "missing-source")

    def test_entity_declarations_are_rejected(self):
        body = b'''<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss><channel><item><title>&xxe;</title></item></channel></rss>'''
        with patch.object(collector_module.socket, "getaddrinfo", fixed_dns):
            result = self._collector(body).collect(["fixture"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["sourceResults"][0]["status"], "error")
        self.assertIn("DOCTYPE", result["errors"][0]["error"])

    def test_atom_entries_are_normalized(self):
        with patch.object(collector_module.socket, "getaddrinfo", fixed_dns):
            result = self._collector(ATOM_FEED).collect(["fixture"], ["technology"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["sourceVersion"], "tag:public.example,2026:tech-1")
        self.assertEqual(result["items"][0]["body"], "Open technology evidence")

    def test_private_literal_source_is_rejected_even_with_test_opener(self):
        source = {"id": "private", "name": "Private", "url": "https://127.0.0.1/feed.xml", "category": "security-research"}
        collector = OSINTCollector([source], opener=lambda request, timeout: FakeResponse(FEED, request.full_url))
        result = collector.collect(["private"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["sourceResults"][0]["status"], "error")
        self.assertIn("private", result["errors"][0]["error"])

    def test_redirect_handler_rejects_other_host(self):
        handler = collector_module._SafeRedirectHandler({"feeds.example.test"})
        with patch.object(collector_module.socket, "getaddrinfo", fixed_dns):
            with self.assertRaises(OSINTCollectionError):
                handler.redirect_request(None, None, 302, "Found", {}, "https://attacker.example/steal")

    def test_byte_cap_is_enforced(self):
        with patch.object(collector_module.socket, "getaddrinfo", fixed_dns):
            result = self._collector(max_bytes=16_384).collect(["fixture"])
        # Fixture is smaller than the enforced minimum cap; make the fake
        # response exceed the cap after initialization.
        self.assertEqual(result["sourceResults"][0]["status"], "ok")
        huge = self._collector(FEED + b"x" * 20_000, max_bytes=16_384)
        with patch.object(collector_module.socket, "getaddrinfo", fixed_dns):
            capped = huge.collect(["fixture"])
        self.assertEqual(capped["sourceResults"][0]["status"], "error")
        self.assertIn("byte cap", capped["errors"][0]["error"])

    def test_invalid_limits_and_html_helper(self):
        with self.assertRaises(OSINTCollectionError):
            self._collector().collect(["fixture"], since_days=-1)
        self.assertEqual(strip_html("<p>A&nbsp;<b>claim</b></p><style>x</style>"), "A claim")

    def test_explicit_rss_mode_does_not_switch_selected_feed_to_search(self):
        from unittest.mock import Mock
        collector = self._collector()
        collector.search_provider = Mock()
        with patch.dict("os.environ", {"CRK_SEARCH_PROVIDER": "rss"}):
            result = collector.collect(["fixture"], ["budget"])
        self.assertEqual(len(result["items"]), 1)
        collector.search_provider.search.assert_not_called()

    def test_missing_key_for_selected_scope_fails_without_rss_fallback(self):
        collector = self._collector()
        collector.search_provider = None
        with patch.dict("os.environ", {"FIRECRAWL_API_KEY": ""}), patch.object(collector, "_fetch", side_effect=AssertionError("RSS fallback forbidden")):
            with self.assertRaisesRegex(OSINTCollectionError, "not configured"):
                collector.collect(["defense-official"], ["equipment"])

    def test_automated_round_budget_scales_with_target(self):
        rounds_25, policy_25 = _automated_round_budget(25, 6)
        rounds_50, policy_50 = _automated_round_budget(50, 6)
        self.assertEqual(rounds_25, 13)
        self.assertEqual(rounds_50, 24)
        self.assertEqual(policy_25["formula"], "min(24, ceil(maxItems / expectedRetainedItemsPerRound))")
        self.assertEqual(policy_50["expectedRetainedItemsPerRound"], 2)

    def test_image_query_rotates_subject_anchor(self):
        provider = FirecrawlSearchProvider(api_key="fixture-token")
        scope = {"id": "public-imagery", "searchType": "images", "domains": ["nasa.gov"], "queryTerms": "(imagery)", "category": "public-imagery", "sourceRole": "public-imagery-archive"}
        first = provider._query("UAS", ['(UAS OR UAV)'], scope=scope, since_days=0, round_number=0)
        second = provider._query("UAS", ['(UAS OR UAV)'], scope=scope, since_days=0, round_number=1)
        self.assertNotEqual(first, second)
        self.assertIn('"UAS"', second)

    def test_automatic_target_counts_text_and_keeps_image_auxiliary(self):
        provider = FirecrawlSearchProvider(api_key="fixture-token")
        calls = []
        def fake_scope(question, keywords, *, since_days, max_items, scope, selected_scopes, now=None, round_number=0, scrape_cache=None):
            calls.append((round_number, max_items))
            image = round_number == 0
            count = 1 if image else 3
            items = [{"title": "Image clue" if image else "正文材料", "itemUri": "https://nasa.gov/" + ("image" if image else "text" + str(index)), "contentHash": "hash-" + str(round_number) + str(index), "publishedAt": None, "sourceId": scope["id"], "provenance": {"contentKind": "image-search-metadata" if image else "markdown", "retrievalKind": "search-discovery-only" if image else "public-web-material", "mediaReferences": []}} for index in range(count)]
            source = {"itemCount": count, "rawResultCount": count, "responseBytes": 0, "searchResponseBytes": 0, "scrapeResponseBytes": 0, "scrapeRequestCount": 0, "searchRetryCount": 0, "excludedCounts": {}, "query": "fixture"}
            return {"items": items, "sourceResults": [source]}
        scope_id = "public-imagery"
        with patch.object(provider, "_search_scope", side_effect=fake_scope):
            result = provider.search("UAS", ['(UAS OR UAV)'], since_days=0, max_items=3, source_ids=[scope_id], ensure_target=True)
        self.assertTrue(result["targetReached"])
        self.assertEqual(result["budget"]["textItems"], 3)
        self.assertEqual(result["budget"]["auxiliaryItems"], 1)
        self.assertEqual(len(result["items"]), 4)
        self.assertGreaterEqual(len(calls), 2)

    def test_automatic_search_skips_identical_paid_query(self):
        provider = FirecrawlSearchProvider(api_key="fixture-token")
        def fake_scope(question, keywords, *, since_days, max_items, scope, selected_scopes, now=None, round_number=0, scrape_cache=None):
            return {"items": [], "sourceResults": [{"itemCount": 0, "rawResultCount": 0, "responseBytes": 0, "searchResponseBytes": 0, "scrapeResponseBytes": 0, "scrapeRequestCount": 0, "searchRetryCount": 0, "excludedCounts": {}, "query": "fixture"}]}
        with patch.object(provider, "_search_scope", side_effect=fake_scope):
            result = provider.search("equipment", [], since_days=0, max_items=5, source_ids=["defense-official"], ensure_target=True)
        self.assertEqual(result["budget"]["executedRequests"], 1)
        self.assertTrue(any(plan["status"] == "skipped-duplicate-query" for plan in result["queryPlan"]))

    def test_publication_date_accepts_explicit_metadata_year_and_keeps_its_origin(self):
        published, origin = collector_module._publication_date({"publishedTime": "October 8", "article:published_time": "2026-10-08T14:30:00-04:00", "dateModified": "2026-11-01"})
        self.assertEqual(published, "2026-10-08T18:30:00Z")
        self.assertEqual(origin["field"], "article:published_time")
        self.assertEqual(origin["rawValue"], "2026-10-08T14:30:00-04:00")
        self.assertEqual(collector_module._publication_date({"dateModified": "2026-11-01", "fetchedAt": "2026-11-10"}), (None, {}))

    def test_publication_date_recovers_header_and_wire_dateline_with_exact_quote(self):
        cases = [
            ("UAV program", "# UAV program\n\n22 January, 2026\n\nA future milestone occurs in 2027.", "2026-01-22T00:00:00Z", "article-header-date"),
            ("UAV program", "# UAV program\n\nApril 24, 2026, 10:41 a.m.\n\nPublic report.", "2026-04-24T00:00:00Z", "article-header-date"),
            ("Navy CCA", "# Navy CCA\n\n**SAN DIEGO – 17 October 2025** – The company has a new contract.", "2025-10-17T00:00:00Z", "press-release-dateline"),
            ("防务研究", "# 防务研究\n\n发布日期：2026年10月9日\n\n正文。", "2026-10-09T00:00:00Z", "published-date-label"),
        ]
        for title, body, expected, basis in cases:
            with self.subTest(body=body):
                published, origin = collector_module._publication_date({}, body=body, title=title)
                self.assertEqual(published, expected)
                self.assertEqual(origin["basis"], basis)
                self.assertEqual(origin["quote"], "\n".join(body.splitlines()[origin["lineStart"] - 1:origin["lineEnd"]]))
                self.assertEqual(origin["precision"], "day")

    def test_publication_date_uses_published_label_instead_of_other_dates(self):
        body = "# Contract opportunity\n\nResponse Date\n\n##### Jan 22, 2026 3:00 PM EST\n\nPublished Date\n\n##### Jan 20, 2026 11:27 AM EST"
        published, origin = collector_module._publication_date({}, body=body, title="Contract opportunity")
        self.assertEqual(published, "2026-01-20T00:00:00Z")
        self.assertEqual(origin["basis"], "published-date-label")
        self.assertIn("Published Date", origin["quote"])

    def test_publication_date_does_not_invent_year_or_use_events_links_or_related_news(self):
        bodies = [
            "# UAV program\n\nOctober 8\n\nThe program is developing.",
            "# UAV program\n\nOn June 6, 2025, an executive order was signed.",
            "# UAV program\n\n![Photo](https://media.example/2025/10/08/test.jpg)",
            "# UAV program\n\n## Related Stories\n\nOctober 8, 2026\n\nAnother article.",
            "# UAV program\n\nPhoto taken: November 20, 2025\n\nThe operator participated in a test.",
            "# UAV program\n\nResponse Date\n\nJan 22, 2026 3:00 PM EST\n\nThe opportunity is open.",
            "# UAV program\n\n## References\n\nJune 6, 2025\n\nExecutive order.",
            "June 6, 2025\n\nThe document quotes an earlier source without its own publication header.",
        ]
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(collector_module._publication_date({}, body=body, title="UAV program"), (None, {}))

    def test_recovered_publication_date_applies_time_window_and_preserves_body(self):
        from unittest.mock import Mock
        body = "# UAV program\n\nSeptember 29, 2026\n\nPublic UAV evidence."
        rows = [{"title": "UAV program", "url": "https://defense.gov/uav", "markdown": body}]
        opener = Mock()
        opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(json.dumps({"success": True, "data": rows}).encode())
        provider = FirecrawlSearchProvider(api_key="fixture-token", opener=opener)
        old = provider.search("UAV", [], since_days=30, max_items=1, source_ids=["defense-official"], now=datetime(2026, 11, 10, tzinfo=timezone.utc))
        self.assertEqual(old["items"], [])
        self.assertEqual(old["sourceResults"][0]["excludedCounts"]["outsideTimeWindow"], 1)
        kept = provider.search("UAV", [], since_days=90, max_items=1, source_ids=["defense-official"], now=datetime(2026, 11, 10, tzinfo=timezone.utc))["items"][0]
        self.assertEqual(kept["publishedAt"], "2026-09-29T00:00:00Z")
        self.assertEqual(kept["body"], body)
        self.assertTrue(kept["provenance"]["publicationDatePresent"])
        self.assertFalse(kept["provenance"]["publicationDateVerified"])
        self.assertEqual(kept["provenance"]["publicationDateEvidence"]["quote"], "September 29, 2026")


if __name__ == "__main__":
    unittest.main()
