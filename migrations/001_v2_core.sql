CREATE TABLE IF NOT EXISTS roles (
    name TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at INTEGER NOT NULL,
    last_login_at INTEGER
);

CREATE TABLE IF NOT EXISTS admin_account_roles (
    admin_id INTEGER NOT NULL REFERENCES admin_accounts(id) ON DELETE CASCADE,
    role_name TEXT NOT NULL REFERENCES roles(name),
    PRIMARY KEY (admin_id, role_name)
);

CREATE TABLE IF NOT EXISTS v2_admin_sessions (
    token_hash TEXT PRIMARY KEY,
    admin_id INTEGER NOT NULL REFERENCES admin_accounts(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_admin_id INTEGER REFERENCES admin_accounts(id),
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    success INTEGER NOT NULL,
    before_json TEXT,
    after_json TEXT,
    request_id TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS admin_audit_time_idx ON admin_audit_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS registration_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_hash TEXT NOT NULL UNIQUE,
    code_prefix TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    expires_at INTEGER,
    max_uses INTEGER,
    used_count INTEGER NOT NULL DEFAULT 0,
    revoked_at INTEGER,
    created_by INTEGER REFERENCES admin_accounts(id),
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS registration_code_uses (
    code_id INTEGER NOT NULL REFERENCES registration_codes(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    used_at INTEGER NOT NULL,
    PRIMARY KEY (code_id, user_id)
);

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    question_type TEXT NOT NULL,
    difficulty TEXT,
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'published' CHECK (status IN ('draft', 'published', 'unpublished')),
    current_revision_id INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    deleted_at INTEGER
);

CREATE TABLE IF NOT EXISTS question_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL REFERENCES questions(id),
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    change_summary TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES admin_accounts(id),
    created_at INTEGER NOT NULL,
    UNIQUE (question_id, version),
    UNIQUE (question_id, content_hash)
);
CREATE INDEX IF NOT EXISTS question_revision_question_idx ON question_revisions(question_id, version DESC);

CREATE TABLE IF NOT EXISTS practice_sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    mode TEXT NOT NULL CHECK (mode IN ('practice', 'memorize')),
    topic TEXT NOT NULL DEFAULT '',
    question_ids_json TEXT NOT NULL,
    current_index INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'abandoned')),
    started_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    completed_at INTEGER
);
CREATE INDEX IF NOT EXISTS practice_sessions_user_idx ON practice_sessions(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS practice_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES practice_sessions(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    question_id TEXT NOT NULL REFERENCES questions(id),
    revision_id INTEGER NOT NULL REFERENCES question_revisions(id),
    submission_id TEXT NOT NULL,
    selected_json TEXT NOT NULL,
    correct INTEGER NOT NULL,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    answered_at INTEGER NOT NULL,
    UNIQUE (session_id, submission_id),
    UNIQUE (session_id, question_id)
);
CREATE INDEX IF NOT EXISTS practice_answers_user_idx ON practice_answers(user_id, answered_at DESC);

CREATE TABLE IF NOT EXISTS question_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    question_id TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'processing', 'resolved', 'closed')),
    resolution TEXT NOT NULL DEFAULT '',
    handled_by INTEGER REFERENCES admin_accounts(id),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS exam_papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL,
    pass_score REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'unpublished')),
    created_by INTEGER REFERENCES admin_accounts(id),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS exam_paper_items (
    paper_id TEXT NOT NULL REFERENCES exam_papers(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    revision_id INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    points REAL NOT NULL,
    grading_rule TEXT NOT NULL DEFAULT 'exact',
    PRIMARY KEY (paper_id, position)
);

CREATE TABLE IF NOT EXISTS exam_attempts (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES exam_papers(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'submitted', 'expired')),
    started_at INTEGER NOT NULL,
    deadline_at INTEGER NOT NULL,
    submitted_at INTEGER,
    score REAL,
    UNIQUE (id, user_id)
);
CREATE INDEX IF NOT EXISTS exam_attempts_user_idx ON exam_attempts(user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS exam_attempt_items (
    attempt_id TEXT NOT NULL REFERENCES exam_attempts(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    revision_id INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    points REAL NOT NULL,
    grading_rule TEXT NOT NULL,
    PRIMARY KEY (attempt_id, position)
);

CREATE TABLE IF NOT EXISTS exam_answers (
    attempt_id TEXT NOT NULL REFERENCES exam_attempts(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    selected_json TEXT NOT NULL,
    correct INTEGER,
    answered_at INTEGER NOT NULL,
    PRIMARY KEY (attempt_id, position)
);

CREATE TABLE IF NOT EXISTS content_articles (
    id TEXT PRIMARY KEY,
    content_type TEXT NOT NULL CHECK (content_type IN ('article', 'announcement', 'changelog')),
    title TEXT NOT NULL,
    body_text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'unpublished')),
    created_by INTEGER REFERENCES admin_accounts(id),
    published_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS backup_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    quick_check TEXT NOT NULL,
    restore_verified INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
