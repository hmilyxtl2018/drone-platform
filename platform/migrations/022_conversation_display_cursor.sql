-- Conversation display state is a cursor, not destructive deletion.  It
-- allows the workbench to clear the visible transcript while retaining the
-- complete immutable message history for audit and replay.
ALTER TABLE ontology_conversation_session ADD COLUMN display_cursor INTEGER NOT NULL DEFAULT 0 CHECK(display_cursor >= 0);
ALTER TABLE ontology_conversation_session ADD COLUMN display_cleared_at TEXT;
