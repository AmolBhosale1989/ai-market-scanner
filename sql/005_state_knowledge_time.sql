-- Stamp insertion time, not transaction start (which can precede lock waits).
ALTER TABLE state_document ALTER COLUMN created_at SET DEFAULT clock_timestamp();
CREATE INDEX IF NOT EXISTS ix_state_document_asof
ON state_document (namespace, document_key, created_at, revision DESC);
