ALTER TABLE exam_papers ADD COLUMN deleted_at INTEGER;
CREATE TABLE IF NOT EXISTS appearance_preferences (
  owner_type TEXT NOT NULL CHECK(owner_type IN ('user','admin')),
  owner_id INTEGER NOT NULL,
  preferences_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(owner_type,owner_id)
);
CREATE TABLE IF NOT EXISTS ai_documents (
  owner_type TEXT NOT NULL,
  owner_id INTEGER NOT NULL,
  cache_key TEXT NOT NULL,
  body_text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at INTEGER NOT NULL,
  PRIMARY KEY(owner_type,owner_id,cache_key)
);
CREATE TABLE IF NOT EXISTS motivation_history (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  source TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(user_id,content)
);
