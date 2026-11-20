import json, sqlite3, tempfile, unittest
from pathlib import Path
from manufacturing_contract import validate_document
from intelligence_store import IntelligenceStore
from manufacturing_store import ManufacturingStore

ROOT = Path(__file__).resolve().parents[1] / 'ontology' / 'coast-raid-k'

class ManufacturingContractTests(unittest.TestCase):
    def test_valid_candidate_document(self):
        document = {
            'schemaVersion': 'uav-manufacturing-extraction.v1',
            'entity': {'entityId': 'equipment-test', 'name': 'Test UAV'},
            'observations': [{'category': 'cost', 'configuration': 'Block 1', 'data': {'costType': 'flyaway', 'amount': 2.5, 'currency': 'USD'}, 'evidence': {'sourceUri': 'https://example.gov/report', 'quote': 'The flyaway cost is $2.5 million.'}}]
        }
        self.assertEqual(validate_document(document), [])

    def test_evidence_and_category_are_required(self):
        errors = validate_document({'schemaVersion': 'uav-manufacturing-extraction.v1', 'entity': {'entityId': 'x', 'name': 'X'}, 'observations': [{'data': {}}]})
        self.assertTrue(any('category' in error for error in errors))
        self.assertTrue(any('evidence' in error for error in errors))

    def test_category_data_shapes_are_enforced(self):
        document = {'schemaVersion': 'uav-manufacturing-extraction.v1', 'entity': {'entityId': 'x', 'name': 'X'}, 'observations': [
            {'category': 'production', 'data': {'quantity': 4}, 'evidence': {'sourceUri': 'https://example.gov', 'quote': 'Four delivered'}}
        ]}
        self.assertTrue(any('recordType' in error for error in validate_document(document)))

    def test_candidate_category_migration_preserves_rows_and_review_links(self):
        migration = ROOT.parent.parent / 'platform' / 'migrations' / '028_manufacturing_candidate_categories.sql'
        with tempfile.TemporaryDirectory() as tmp:
            con = sqlite3.connect(Path(tmp) / 'legacy.sqlite')
            con.execute('PRAGMA foreign_keys=ON')
            con.executescript('''
                CREATE TABLE ontology_manufacturing_candidate (
                  candidate_id TEXT PRIMARY KEY, entity_id TEXT NOT NULL,
                  candidate_type TEXT NOT NULL CHECK(candidate_type IN ('production','cost')),
                  payload_json TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}',
                  source_snapshot_ids_json TEXT NOT NULL DEFAULT '[]', content_hash TEXT NOT NULL UNIQUE,
                  status TEXT NOT NULL DEFAULT 'under-review', source_import_id TEXT,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX ontology_manufacturing_candidate_entity_idx ON ontology_manufacturing_candidate(entity_id,candidate_type,status,created_at DESC);
                CREATE TABLE ontology_manufacturing_candidate_review (
                  review_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES ontology_manufacturing_candidate(candidate_id),
                  action TEXT NOT NULL, reviewer TEXT NOT NULL, reason TEXT NOT NULL,
                  edited_payload_json TEXT, created_at TEXT NOT NULL);
                CREATE INDEX ontology_manufacturing_candidate_review_idx ON ontology_manufacturing_candidate_review(candidate_id,created_at DESC);
                INSERT INTO ontology_manufacturing_candidate VALUES('c1','e1','cost','{}','{}','[]','hash1','accepted',NULL,'t1','t2');
                INSERT INTO ontology_manufacturing_candidate_review VALUES('r1','c1','accept','qa','checked',NULL,'t2');
            ''')
            con.executescript(migration.read_text(encoding='utf-8'))
            self.assertEqual(con.execute('SELECT candidate_type,status FROM ontology_manufacturing_candidate WHERE candidate_id="c1"').fetchone(), ('cost','accepted'))
            self.assertEqual(con.execute('SELECT candidate_id FROM ontology_manufacturing_candidate_review WHERE review_id="r1"').fetchone(), ('c1',))
            self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(), [])
            con.close()

    def test_all_five_categories_flow_through_review_and_publication(self):
        document = {
            'schemaVersion': 'uav-manufacturing-extraction.v1',
            'entity': {'entityId': 'equipment-test', 'name': 'Test UAV'},
            'observations': [
                {'category': 'composition', 'configuration': 'Block 1', 'data': {'partName': 'Airframe', 'relation': 'partOfAircraft'}, 'evidence': {'sourceUri': 'https://example.gov/a', 'quote': 'Airframe'}},
                {'category': 'process', 'configuration': 'Block 1', 'data': {'processName': 'Final assembly', 'processType': 'assembly', 'sequenceNo': 1}, 'evidence': {'sourceUri': 'https://example.gov/b', 'quote': 'Final assembly'}},
                {'category': 'organization', 'data': {'organizationName': 'Example Works', 'role': 'manufacturer'}, 'evidence': {'sourceUri': 'https://example.gov/c', 'quote': 'Manufactured by Example Works'}},
                {'category': 'production', 'data': {'recordType': 'delivery', 'quantity': 4, 'quantityUnit': 'aircraft'}, 'evidence': {'sourceUri': 'https://example.gov/d', 'quote': 'Four delivered'}},
                {'category': 'cost', 'data': {'costType': 'flyaway', 'amount': 2.5, 'currency': 'USD'}, 'evidence': {'sourceUri': 'https://example.gov/e', 'quote': 'Flyaway cost'}},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = IntelligenceStore(Path(tmp) / 'manufacturing.sqlite')
            manufacturing = ManufacturingStore(store)
            result = manufacturing.ingest_extraction(document, 'equipment-test')
            self.assertEqual(result['candidateCount'], 5)
            for candidate in result['candidates']:
                reviewed = manufacturing.review_candidate(candidate['candidate_id'], {'action': 'accept', 'reviewer': 'qa', 'reason': 'contract test'})
                self.assertEqual(reviewed['status'], 'accepted')
                published = manufacturing.publish_candidate(candidate['candidate_id'], {'publishedBy': 'qa', 'reason': 'contract test'})
                self.assertTrue(published['record'])
            records = manufacturing.get('equipment-test')
            self.assertEqual(len(records['assembly']), 1)
            self.assertEqual(len(records['processes']), 1)
            self.assertEqual(len(records['organizationRoles']), 1)
            self.assertEqual(len(records['production']), 1)
            self.assertEqual(len(records['costs']), 1)
            self.assertEqual(len(records['configurations']), 1)
            with store._connect() as con:
                self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_accepted_candidate_is_projected_into_read_tabs_before_publication(self):
        document = {
            'schemaVersion': 'uav-manufacturing-extraction.v1',
            'entity': {'entityId': 'equipment-test', 'name': 'Test UAV'},
            'observations': [{
                'category': 'process',
                'configuration': 'Block 1',
                'data': {'processName': 'Final assembly', 'processType': 'assembly'},
                'evidence': {'sourceUri': 'https://example.gov/a', 'snapshotId': 'snapshot-1', 'quote': 'Final assembly'},
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = IntelligenceStore(Path(tmp) / 'manufacturing.sqlite')
            manufacturing = ManufacturingStore(store)
            candidate = manufacturing.ingest_extraction(document, 'equipment-test')['candidates'][0]
            manufacturing.review_candidate(candidate['candidate_id'], {
                'action': 'accept', 'reviewer': 'system-auto-review', 'reason': 'evidence contract passed',
            })
            records = manufacturing.get('equipment-test')
            self.assertEqual(records['acceptedCandidateCount'], 1)
            self.assertEqual(len(records['processes']), 1)
            self.assertEqual(records['processes'][0]['process_name'], 'Final assembly')
            self.assertEqual(records['processes'][0]['review_status'], 'accepted')
            self.assertEqual(records['processes'][0]['record_state'], 'accepted-not-published')
            self.assertEqual(records['production'], [])
            self.assertEqual(records['costs'], [])

if __name__ == '__main__': unittest.main()
