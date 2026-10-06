-- Extensions enabled at database creation.
--
-- Runs once, on an empty data directory only. That "only" matters: the
-- pgvector/pgvector image ships the extension files but does not create the
-- extension, so an init script is the only way to get it without a manual
-- migration step. A later change to this file will not apply to an existing
-- volume; that is intentional, because extension creation on a live database
-- belongs in a reviewed Alembic migration instead.

-- pgvector backs template-embedding similarity search (FR-2). Created with a
-- generous dimension ceiling: the curated templates are embedded at 1536d, and
-- ivfflat/cassandra index limits are expressed as a fraction of the vector
-- column's dimension, so raising this later would require a re-index.
CREATE EXTENSION IF NOT EXISTS vector;

-- uuid-ossp and pgcrypto give us gen_random_uuid() via pgcrypto, which is what
-- the submission and question identifiers use. Better than a Python-side UUID
-- because the database can then guarantee uniqueness under concurrent inserts
-- without the application having to retry.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Case-insensitive text comparison for candidate email lookup. Created here
-- rather than per-query so the index can be used.
CREATE EXTENSION IF NOT EXISTS citext;
