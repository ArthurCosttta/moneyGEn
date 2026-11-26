CREATE TABLE IF NOT EXISTS calendar_items(
 id TEXT PRIMARY KEY,
 scheduled_date TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('video','edit','ad','post','note')),
 title TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PLANNED',
 batch_id TEXT REFERENCES batches(id) ON DELETE SET NULL,
 media_path TEXT NOT NULL DEFAULT '',
 meta_account_id TEXT NOT NULL DEFAULT '',
 meta_campaign_id TEXT NOT NULL DEFAULT '',
 meta_ad_id TEXT NOT NULL DEFAULT '',
 platform TEXT NOT NULL DEFAULT 'Meta Ads',
 notes TEXT NOT NULL DEFAULT '',
 metrics_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calendar_date ON calendar_items(scheduled_date);
