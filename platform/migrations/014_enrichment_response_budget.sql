-- Per-entity response-size budget is recorded with the batch item. The
-- Firecrawl adapter also keeps its hard 4 MB safety ceiling.
ALTER TABLE ontology_capability_batch_item ADD COLUMN max_response_bytes INTEGER NOT NULL DEFAULT 4000000 CHECK(max_response_bytes >= 65536 AND max_response_bytes <= 4000000);
