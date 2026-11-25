PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batches(
 id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision>=1), state TEXT NOT NULL,
 name TEXT NOT NULL, defaults_json TEXT NOT NULL, draft_text TEXT NOT NULL DEFAULT '',
 max_credits INTEGER CHECK(max_credits IS NULL OR max_credits>=0),
 plan_hash TEXT, output_dir TEXT NOT NULL, expected_account TEXT NOT NULL,
 pause_requested INTEGER NOT NULL DEFAULT 0 CHECK(pause_requested IN(0,1)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assets(
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, path TEXT NOT NULL UNIQUE,
 mime TEXT NOT NULL, bytes INTEGER NOT NULL CHECK(bytes>0),
 width INTEGER NOT NULL CHECK(width>0), height INTEGER NOT NULL CHECK(height>0),
 source TEXT NOT NULL CHECK(source IN('uploaded','generated')),
 source_execution_id TEXT REFERENCES executions(id));
CREATE TABLE IF NOT EXISTS items(
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id), position INTEGER NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('i2v','t2v')), prompt TEXT NOT NULL,
 asset_id TEXT REFERENCES assets(id), settings_json TEXT NOT NULL,
 output_name TEXT NOT NULL, state TEXT NOT NULL, source_index INTEGER,
 CHECK((kind='i2v' AND asset_id IS NOT NULL) OR (kind='t2v' AND asset_id IS NULL)),
 UNIQUE(batch_id,position), UNIQUE(batch_id,output_name COLLATE NOCASE));
CREATE TABLE IF NOT EXISTS plans(
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id), revision INTEGER NOT NULL,
 hash TEXT NOT NULL, snapshot_json TEXT NOT NULL, quote_json TEXT NOT NULL,
 validation_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(batch_id,revision,hash));
CREATE TABLE IF NOT EXISTS approvals(
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id), revision INTEGER NOT NULL,
 plan_hash TEXT NOT NULL, scope TEXT NOT NULL CHECK(scope IN('TEST','REMAINDER','IMAGE')),
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS executions(
 id TEXT PRIMARY KEY, batch_id TEXT REFERENCES batches(id),
 item_id TEXT REFERENCES items(id), attempt INTEGER NOT NULL,
 capability TEXT NOT NULL, state TEXT NOT NULL, correlation_id TEXT NOT NULL,
 request_hash TEXT NOT NULL, approval_id TEXT REFERENCES approvals(id),
 intent_at TEXT, provider_operation_id TEXT, lease_owner TEXT, lease_until TEXT,
 error_code TEXT, result_json TEXT,
 CHECK((capability='video.generate' AND item_id IS NOT NULL)
    OR capability IN('image.generate','media.export','flow.upload','flow.inspect','maintenance.install')),
 CHECK(batch_id IS NOT NULL OR capability='maintenance.install'),
 UNIQUE(item_id,attempt));
CREATE TABLE IF NOT EXISTS uploads(
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id), asset_id TEXT NOT NULL REFERENCES assets(id),
 project_id TEXT NOT NULL, media_id TEXT, state TEXT NOT NULL, sha256 TEXT NOT NULL,
 result_json TEXT, UNIQUE(batch_id,asset_id,project_id));
CREATE TABLE IF NOT EXISTS artifacts(
 id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id), sha256 TEXT NOT NULL,
 mime TEXT NOT NULL, bytes INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
 duration REAL NOT NULL, path TEXT NOT NULL UNIQUE, validation_state TEXT NOT NULL,
 decoder_report TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, batch_id TEXT REFERENCES batches(id),
 item_id TEXT REFERENCES items(id), execution_id TEXT REFERENCES executions(id),
 correlation_id TEXT NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS idempotency(
 key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, status_code INTEGER,
 response_json TEXT, execution_id TEXT REFERENCES executions(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports(
 id TEXT PRIMARY KEY, preview_hash TEXT NOT NULL, batch_id TEXT REFERENCES batches(id),
 preview_json TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value_json TEXT NOT NULL, revision INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_items_queue ON items(state,batch_id,position);
CREATE INDEX IF NOT EXISTS idx_events_batch ON events(batch_id,id);
CREATE INDEX IF NOT EXISTS idx_executions_state ON executions(state,lease_until);
