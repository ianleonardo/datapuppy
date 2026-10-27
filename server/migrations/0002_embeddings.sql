-- 0002_embeddings.sql — vector tables; {embed_dim} is substituted from rag.embed_dim (TS §7.1, §8.3).
-- A different dimension later is handled by the re-index job (doc_chunks_new / schema_cards_new swap).

CREATE SEQUENCE doc_chunk_seq;
CREATE TABLE doc_chunks (
  id BIGINT PRIMARY KEY DEFAULT nextval('doc_chunk_seq'),
  document_id UUID NOT NULL,                       -- → documents.id
  scope rag_scope NOT NULL, owner_ref UUID NOT NULL, country_code VARCHAR(2), connection_id UUID,
  chunk_index INTEGER NOT NULL, content VARCHAR NOT NULL, token_count INTEGER NOT NULL,
  meta JSON NOT NULL DEFAULT '{}',
  embedding FLOAT[{embed_dim}]                      -- NULL only while degraded (no embedding provider)
);

CREATE TABLE schema_cards (
  table_id UUID PRIMARY KEY,                       -- → schema_tables.id
  connection_id UUID NOT NULL, snapshot_id UUID NOT NULL,
  card_text VARCHAR NOT NULL,
  embedding FLOAT[{embed_dim}]
);
