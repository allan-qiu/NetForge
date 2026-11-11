CREATE TABLE IF NOT EXISTS user_admin_edits (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    metric_deltas_json TEXT NOT NULL DEFAULT '{}',
    revision TEXT NOT NULL,
    reason TEXT NOT NULL,
    updated_by INTEGER NOT NULL REFERENCES admin_accounts(id),
    updated_at INTEGER NOT NULL
);
