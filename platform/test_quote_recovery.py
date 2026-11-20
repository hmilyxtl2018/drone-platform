"""Whitespace repair must preserve immutable quotes and single-window boundaries."""

import hashlib
import unittest

try:
    from evidence_graph import extract_research_graph
    from source_excerpts import recover_selected_quote, selected_quote_offset
except ModuleNotFoundError:
    from .evidence_graph import extract_research_graph
    from .source_excerpts import recover_selected_quote, selected_quote_offset


class QuoteRecoveryTests(unittest.TestCase):
    def test_recover_multiline_whitespace_with_original_unicode_offsets(self):
        body = "😀 Header.\nAcme\t developed\n\nCamera X using an\u00a0open interface.\nEnd."
        selection = {"excerpts": [{"startChar": 10, "endChar": len(body), "text": body[10:]}]}
        quote = "Acme developed Camera X using an open interface."
        self.assertIsNone(selected_quote_offset(body, selection, quote))
        start, recovered = recover_selected_quote(body, selection, quote)
        self.assertEqual(start, body.index("Acme"))
        self.assertEqual(recovered, "Acme\t developed\n\nCamera X using an\u00a0open interface.")
        self.assertEqual(body[start:start + len(recovered)], recovered)

    def test_paraphrase_punctuation_case_and_removed_word_boundaries_reject(self):
        body = "Acme\ndeveloped Camera X."
        selection = {"excerpts": [{"startChar": 0, "endChar": len(body), "text": body}]}
        for quote in ("Acme built Camera X.", "Acme developed Camera X!", "ACME developed Camera X.", "Acmedeveloped Camera X."):
            with self.subTest(quote=quote):
                self.assertIsNone(recover_selected_quote(body, selection, quote))

    def test_adjacent_and_separated_windows_are_never_joined(self):
        body = "Acme\ndeveloped Camera X."
        for split in (5, 10):
            selection = {"excerpts": [{"startChar": 0, "endChar": split, "text": body[:split]},
                                      {"startChar": split, "endChar": len(body), "text": body[split:]}]}
            self.assertIsNone(recover_selected_quote(body, selection, "Acme developed Camera X."))
        selection = {"excerpts": [{"startChar": 0, "endChar": 5, "text": body[:5]},
                                  {"startChar": 15, "endChar": len(body), "text": body[15:]}]}
        self.assertIsNone(recover_selected_quote(body, selection, "Acme Camera X."))

    def test_graph_restores_node_edge_quotes_and_exposes_sanitized_reasons(self):
        body = "😀 Acme\n developed Camera X."
        source = {"itemId": "s1", "body": body, "sourceUri": "https://example.test/report", "sourceVersion": "v1",
                  "contentHash": hashlib.sha256(body.encode()).hexdigest()}
        quote = "Acme developed Camera X."
        response = {"nodes": [
            {"id": "a", "type": "Organization", "label": "Acme", "itemId": "s1", "quote": quote},
            {"id": "c", "type": "EquipmentModel", "label": "Camera X", "itemId": "s1", "quote": quote},
            {"id": "bad", "type": "Technology", "label": "secret-from-provider", "itemId": "s1", "quote": "secret-from-provider"},
        ], "edges": [{"from": "a", "to": "c", "relation": "develops", "itemId": "s1", "quote": quote}], "gaps": []}
        graph = extract_research_graph([source], lambda _: response)
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        for record in graph["nodes"] + graph["edges"]:
            ref = record["payload"]["evidenceRefs"][0]
            self.assertEqual(ref["quote"], body[ref["startChar"]:ref["endChar"]])
            self.assertEqual(ref["startChar"], 2)
        self.assertEqual(graph["metadata"]["whitespaceRecoveredQuotes"], 3)
        self.assertEqual(graph["metadata"]["rejectedReasonCounts"], {"node:quote-not-in-selected-window": 1})
        self.assertNotIn("secret-from-provider", str(graph["metadata"]))


if __name__ == "__main__":
    unittest.main()
