CREATE INDEX IF NOT EXISTS registration_codes_active_idx ON registration_codes(revoked_at, expires_at);
CREATE INDEX IF NOT EXISTS questions_filter_idx ON questions(status, topic, question_type, difficulty);
CREATE INDEX IF NOT EXISTS feedback_status_idx ON question_feedback(status, created_at DESC);
CREATE INDEX IF NOT EXISTS exam_papers_status_idx ON exam_papers(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS content_status_idx ON content_articles(content_type, status, published_at DESC);
