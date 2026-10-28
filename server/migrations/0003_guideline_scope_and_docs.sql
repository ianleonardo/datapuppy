-- 0003 — guidelines may apply to every country and database (both targets NULL);
-- documents carry a kind: REFERENCE (References panel) or GUIDELINE (Admin → Guidelines, retrieved as rules).
-- DuckDB cannot drop a CHECK constraint, so the guidelines table is rebuilt.

CREATE TABLE guidelines_v3 (
  id UUID PRIMARY KEY DEFAULT uuid(),
  country_code VARCHAR(2), connection_id UUID, title VARCHAR NOT NULL, body_md VARCHAR NOT NULL,
  priority INTEGER NOT NULL DEFAULT 100, is_active BOOLEAN NOT NULL DEFAULT true
);
INSERT INTO guidelines_v3 SELECT id, country_code, connection_id, title, body_md, priority, is_active FROM guidelines;
DROP TABLE guidelines;
ALTER TABLE guidelines_v3 RENAME TO guidelines;

ALTER TABLE documents ADD COLUMN kind VARCHAR DEFAULT 'REFERENCE';
UPDATE documents SET kind = 'REFERENCE' WHERE kind IS NULL;
