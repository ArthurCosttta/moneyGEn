CREATE TABLE IF NOT EXISTS flow_runs(
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 spec_json TEXT NOT NULL,
 inputs_json TEXT NOT NULL,
 output_dir TEXT NOT NULL,
 state TEXT NOT NULL,
 active_node_id TEXT,
 batch_id TEXT REFERENCES batches(id),
 error_code TEXT,
 error_message TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flow_node_runs(
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES flow_runs(id) ON DELETE CASCADE,
 node_id TEXT NOT NULL,
 position INTEGER NOT NULL,
 node_type TEXT NOT NULL,
 label TEXT NOT NULL,
 state TEXT NOT NULL,
 depends_on_json TEXT NOT NULL,
 config_json TEXT NOT NULL,
 output_json TEXT,
 error_code TEXT,
 error_message TEXT,
 started_at TEXT,
 finished_at TEXT,
 UNIQUE(run_id,node_id)
);
CREATE INDEX IF NOT EXISTS idx_flow_node_runs_run ON flow_node_runs(run_id,position);
