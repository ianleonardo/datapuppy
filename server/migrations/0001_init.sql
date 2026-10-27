-- 0001_init.sql — DuckDB application store (TS §7.3)
-- References are documented as comments (-- → table.col); cascades are enforced in repositories (TR-STO-02).

-- ===== Enums =====
CREATE TYPE user_role      AS ENUM ('SYSTEM_ADMIN','ANALYST');
CREATE TYPE auth_source    AS ENUM ('LOCAL','LDAP');
CREATE TYPE db_engine      AS ENUM ('ORACLE','MARIADB','POSTGRES','DUCKDB');
CREATE TYPE snapshot_state AS ENUM ('CRAWLING','DRAFT','LOCKED','SUPERSEDED','FAILED');
CREATE TYPE table_class    AS ENUM ('FACT','DIMENSION','STAGING','BACKUP','TEMP','ARCHIVE','UNKNOWN');
CREATE TYPE inclusion      AS ENUM ('INCLUDED','EXCLUDED','PENDING_REVIEW');
CREATE TYPE sensitivity    AS ENUM ('PUBLIC','INTERNAL','SENSITIVE','RESTRICTED');
CREATE TYPE rag_scope      AS ENUM ('GLOBAL','COUNTRY','CONNECTION','PRIVATE');
CREATE TYPE doc_status     AS ENUM ('UPLOADED','EXTRACTING','EMBEDDING','READY','REJECTED','FAILED');
CREATE TYPE msg_role       AS ENUM ('USER','ASSISTANT','SYSTEM_EVENT');
CREATE TYPE run_status     AS ENUM ('PENDING','INTERCEPTED','RUNNING','SUCCEEDED','FAILED','CANCELLED','REJECTED');
CREATE TYPE job_status     AS ENUM ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED');
CREATE TYPE audit_status   AS ENUM ('SUCCESS','FAILED','INTERCEPTED');

-- ===== Identity & access =====
CREATE TABLE users (
  id               UUID PRIMARY KEY DEFAULT uuid(),
  user_id          VARCHAR NOT NULL UNIQUE,
  display_name     VARCHAR NOT NULL,
  email            VARCHAR,
  role             user_role NOT NULL DEFAULT 'ANALYST',
  auth_source      auth_source NOT NULL,
  password_hash    VARCHAR,
  must_change_pw   BOOLEAN NOT NULL DEFAULT false,
  pw_changed_at    TIMESTAMPTZ,
  failed_logins    INTEGER NOT NULL DEFAULT 0,
  locked_until     TIMESTAMPTZ,
  ldap_dn          VARCHAR,
  role_from_ldap   BOOLEAN NOT NULL DEFAULT false,
  is_active        BOOLEAN NOT NULL DEFAULT true,
  preferences      JSON NOT NULL DEFAULT '{}',
  last_login_at    TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (auth_source <> 'LOCAL' OR password_hash IS NOT NULL)
);

CREATE TABLE auth_sessions (
  token_hash     VARCHAR PRIMARY KEY,              -- sha256(session token)
  user_ref       UUID NOT NULL,                    -- → users.id
  csrf_token     VARCHAR NOT NULL,
  theme          VARCHAR NOT NULL,                 -- morning|afternoon|night (DD-10)
  client_tz      VARCHAR,
  ip             VARCHAR,
  user_agent     VARCHAR,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at     TIMESTAMPTZ NOT NULL
);

CREATE TABLE countries (
  code VARCHAR(2) PRIMARY KEY, name VARCHAR NOT NULL, is_active BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE user_grants (
  user_ref       UUID NOT NULL,                    -- → users.id
  connection_id  UUID,                             -- → connections.id
  country_code   VARCHAR(2),                       -- → countries.code
  CHECK ((connection_id IS NULL) <> (country_code IS NULL))
);

-- ===== Connections =====
CREATE TABLE connections (
  id                  UUID PRIMARY KEY DEFAULT uuid(),
  name                VARCHAR NOT NULL,
  country_code        VARCHAR(2) NOT NULL,         -- → countries.code
  engine              db_engine NOT NULL,
  params              JSON NOT NULL,
  secret_ciphertext   BLOB NOT NULL,
  secret_nonce        BLOB NOT NULL,
  secret_key_version  INTEGER NOT NULL,
  is_authorized       BOOLEAN NOT NULL DEFAULT false,
  allow_result_values_to_llm BOOLEAN NOT NULL DEFAULT false,
  limits_override     JSON,
  last_test_status    VARCHAR,
  last_tested_at      TIMESTAMPTZ,
  created_by          UUID,
  created_at          TIMESTAMPTZ DEFAULT now(),
  updated_at          TIMESTAMPTZ DEFAULT now(),
  UNIQUE (country_code, name)
);

-- ===== Schema governance =====
CREATE TABLE schema_snapshots (
  id UUID PRIMARY KEY DEFAULT uuid(),
  connection_id UUID NOT NULL, version INTEGER NOT NULL, state snapshot_state NOT NULL,
  job_id UUID, stats JSON, locked_by UUID, locked_at TIMESTAMPTZ, prompt_digest VARCHAR,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (connection_id, version)
  -- invariant (service-enforced): <= 1 LOCKED per connection_id
);

CREATE TABLE schema_tables (
  id UUID PRIMARY KEY DEFAULT uuid(),
  snapshot_id UUID NOT NULL, schema_name VARCHAR NOT NULL, table_name VARCHAR NOT NULL,
  object_type VARCHAR NOT NULL CHECK (object_type IN ('TABLE','VIEW','MATERIALIZED_VIEW')),
  alias VARCHAR, description VARCHAR,
  classification table_class NOT NULL DEFAULT 'UNKNOWN', class_confidence DECIMAL(3,2),
  smell_flags VARCHAR[] NOT NULL DEFAULT [], base_table_hint VARCHAR,
  inclusion inclusion NOT NULL DEFAULT 'INCLUDED', decided_by UUID, decided_at TIMESTAMPTZ,
  row_estimate BIGINT
);

CREATE TABLE schema_columns (
  id UUID PRIMARY KEY DEFAULT uuid(),
  table_id UUID NOT NULL, ordinal INTEGER NOT NULL, column_name VARCHAR NOT NULL,
  data_type VARCHAR NOT NULL, is_nullable BOOLEAN, is_pk BOOLEAN DEFAULT false,
  alias VARCHAR, description VARCHAR,
  sensitivity sensitivity NOT NULL DEFAULT 'INTERNAL', is_masked BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE schema_relationships (
  id UUID PRIMARY KEY DEFAULT uuid(),
  snapshot_id UUID NOT NULL, from_table_id UUID NOT NULL, from_columns VARCHAR[] NOT NULL,
  to_table_id UUID NOT NULL, to_columns VARCHAR[] NOT NULL,
  source VARCHAR NOT NULL CHECK (source IN ('FK_CONSTRAINT','ADMIN_DEFINED','INFERRED')), name VARCHAR
);

CREATE TABLE guidelines (
  id UUID PRIMARY KEY DEFAULT uuid(),
  country_code VARCHAR(2), connection_id UUID, title VARCHAR NOT NULL, body_md VARCHAR NOT NULL,
  priority INTEGER NOT NULL DEFAULT 100, is_active BOOLEAN NOT NULL DEFAULT true,
  CHECK (country_code IS NOT NULL OR connection_id IS NOT NULL)
);

-- ===== RAG =====
CREATE TABLE documents (
  id UUID PRIMARY KEY DEFAULT uuid(),
  owner_ref UUID NOT NULL, scope rag_scope NOT NULL, country_code VARCHAR(2), connection_id UUID,
  filename VARCHAR NOT NULL, mime_type VARCHAR NOT NULL,
  size_bytes BIGINT NOT NULL CHECK (size_bytes <= 52428800),
  sha256 VARCHAR NOT NULL, file_path VARCHAR NOT NULL,
  status doc_status NOT NULL, status_detail VARCHAR, chunk_count INTEGER,
  embedding_model VARCHAR, created_at TIMESTAMPTZ DEFAULT now(),
  CHECK (scope <> 'COUNTRY' OR country_code IS NOT NULL),
  CHECK (scope <> 'CONNECTION' OR connection_id IS NOT NULL)
);

-- ===== Chat =====
CREATE TABLE chat_sessions (
  id UUID PRIMARY KEY DEFAULT uuid(),
  owner_ref UUID NOT NULL, title VARCHAR NOT NULL DEFAULT 'New chat', connection_id UUID,
  created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE chat_messages (
  id UUID PRIMARY KEY DEFAULT uuid(),
  session_id UUID NOT NULL, owner_ref UUID NOT NULL, seq INTEGER NOT NULL, role msg_role NOT NULL,
  content JSON NOT NULL,
  plain_text VARCHAR NOT NULL,
  artifacts JSON NOT NULL DEFAULT '{}',
  llm_meta JSON,
  feedback SMALLINT,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (session_id, seq)
);

CREATE TABLE query_runs (
  id UUID PRIMARY KEY DEFAULT uuid(),
  owner_ref UUID NOT NULL, session_id UUID, message_id UUID, connection_id UUID NOT NULL,
  origin VARCHAR NOT NULL CHECK (origin IN ('AI','MANUAL','REFINED')),
  sql_text VARCHAR NOT NULL, sql_hash VARCHAR NOT NULL, status run_status NOT NULL,
  row_count BIGINT, row_count_method VARCHAR, column_count INTEGER, columns JSON,
  confirmed_large BOOLEAN DEFAULT false, error_code VARCHAR, error_detail VARCHAR,
  started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, duration_ms INTEGER
);

-- ===== Platform =====
CREATE TABLE jobs (
  id UUID PRIMARY KEY DEFAULT uuid(),
  type VARCHAR NOT NULL, payload JSON NOT NULL, status job_status NOT NULL DEFAULT 'QUEUED',
  progress JSON, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
  error VARCHAR, idempotency_key VARCHAR, result JSON, created_by UUID,
  created_at TIMESTAMPTZ DEFAULT now(), started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
  heartbeat_at TIMESTAMPTZ
);

CREATE TABLE llm_providers (
  id UUID PRIMARY KEY DEFAULT uuid(),
  kind VARCHAR NOT NULL CHECK (kind IN ('GEMINI','SPIRARE')),
  display_name VARCHAR NOT NULL,
  chat_url VARCHAR NOT NULL, chat_model VARCHAR NOT NULL,
  embed_url VARCHAR, embed_model VARCHAR, embed_dim INTEGER,
  max_model_len INTEGER,
  max_output_tokens INTEGER,
  verify_ssl BOOLEAN NOT NULL, use_proxy BOOLEAN NOT NULL, proxy_url VARCHAR, ca_bundle_path VARCHAR,
  timeout_s INTEGER NOT NULL DEFAULT 120,
  params JSON NOT NULL DEFAULT '{}',
  api_key_ciphertext BLOB, api_key_nonce BLOB, api_key_version INTEGER,
  is_enabled BOOLEAN NOT NULL DEFAULT true,
  is_primary_chat BOOLEAN NOT NULL DEFAULT false,
  is_primary_embed BOOLEAN NOT NULL DEFAULT false,
  last_test JSON
);

CREATE TABLE app_settings (key VARCHAR PRIMARY KEY, value JSON NOT NULL, updated_by UUID, updated_at TIMESTAMPTZ DEFAULT now());

CREATE TABLE starter_prompts (id INTEGER PRIMARY KEY, label VARCHAR NOT NULL, prompt VARCHAR NOT NULL,
                              intent VARCHAR NOT NULL, sort_order INTEGER NOT NULL, is_active BOOLEAN DEFAULT true);

-- ===== Audit =====
CREATE SEQUENCE audit_seq;
CREATE TABLE audit_log (
  id         BIGINT PRIMARY KEY DEFAULT nextval('audit_seq'),
  ts         TIMESTAMPTZ NOT NULL,
  user_ref   UUID,
  user_id    VARCHAR,
  action     VARCHAR NOT NULL,
  status     audit_status NOT NULL,
  context    JSON NOT NULL,
  prev_hash  VARCHAR NOT NULL,
  row_hash   VARCHAR NOT NULL
);
CREATE INDEX audit_ts_idx ON audit_log(ts);

-- ===== Seed data =====
INSERT INTO starter_prompts VALUES
 (1, 'Structure & ERD Discovery', 'Show me the table structure and relationships for the core module.', 'EXPLAIN_SCHEMA', 1, true),
 (2, 'Time-Series Metrics', 'Generate a SQL query to extract month-over-month growth for Revenue in United States.', 'GENERATE_SQL', 2, true),
 (3, 'Schema Hygiene Check', 'Are there any temporary or backup tables in this schema that I should ignore?', 'HYGIENE_CHECK', 3, true),
 (4, 'Query Refactor & Syntax Check', 'Check this manual SQL query for syntax errors and optimize it.', 'REVIEW_SQL', 4, true),
 (5, 'Automated Data Insight', 'Give me the key insights from the current result.', 'INTERPRET_RESULT', 5, true);

INSERT INTO countries VALUES ('SG', 'Singapore', true), ('MY', 'Malaysia', true), ('ID', 'Indonesia', true),
                             ('TH', 'Thailand', true), ('VN', 'Vietnam', true), ('US', 'United States', true);
