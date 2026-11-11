"""NetForge V2 additive data and service layer.

This module deliberately has no dependency on ``server.py`` so migrations, backups,
and tests can run without starting the HTTP server.  It preserves every legacy table
and adds only new tables or nullable/defaulted columns.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parent
MIGRATIONS_DIR = ROOT / "migrations"
QUESTION_PREFIX = "window.NETWORK_QUESTION_BANK = "

ROLE_PERMISSIONS = {
    "super_admin": {"*"},
    "question_admin": {
        "dashboard.read",
        "questions.read", "questions.write", "questions.import",
        "papers.read", "papers.write", "feedback.read", "feedback.write",
    },
    "support_admin": {
        "users.read", "feedback.read", "feedback.write",
        "content.read", "content.write", "dashboard.read",
    },
}

USER_COLUMNS = {
    "status": "TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'deleted'))",
    "deleted_at": "INTEGER",
    "delete_reason": "TEXT",
    "leaderboard_opt_in": "INTEGER NOT NULL DEFAULT 0",
    "avatar_url": "TEXT",
    "updated_at": "INTEGER",
}

SENSITIVE_KEYS = {"password", "password_hash", "token", "token_hash", "secret", "code_hash"}


class V2Error(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ClosingConnection(sqlite3.Connection):
    """sqlite connection whose context manager also closes the file handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect_db(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=10, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def encode_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(derived).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        method, n, r, p, salt64, digest64 = encoded.split("$")
        if method != "scrypt":
            return False
        salt, expected = base64.b64decode(salt64), base64.b64decode(digest64)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clean_audit(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else _clean_audit(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_audit(item) for item in value]
    return value


class MigrationManager:
    def __init__(self, db_path: str | Path, migrations_dir: str | Path = MIGRATIONS_DIR):
        self.db_path = Path(db_path)
        self.migrations_dir = Path(migrations_dir)

    def apply(self) -> list[str]:
        applied_now: list[str] = []
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with connect_db(self.db_path) as db:
            self._ensure_legacy_schema(db)
            db.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at INTEGER NOT NULL
                   )"""
            )
            existing = {row["version"]: row["checksum"] for row in db.execute("SELECT version, checksum FROM schema_migrations")}
            for path in sorted(self.migrations_dir.glob("*.sql")):
                version = path.stem
                sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                if version in existing:
                    if existing[version] != checksum:
                        raise V2Error("migration_checksum_mismatch", f"迁移 {version} 的校验值已变化")
                    continue
                try:
                    db.execute("BEGIN IMMEDIATE")
                    if version.startswith("001_"):
                        self._ensure_user_columns(db)
                    for statement in (part.strip() for part in sql.split(";")):
                        if statement:
                            db.execute(statement)
                    db.execute(
                        "INSERT INTO schema_migrations(version, checksum, applied_at) VALUES (?, ?, ?)",
                        (version, checksum, int(time.time())),
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
                applied_now.append(version)
            now = int(time.time())
            db.executemany("INSERT OR IGNORE INTO roles(name, created_at) VALUES (?, ?)", [(role, now) for role in ROLE_PERMISSIONS])
        return applied_now

    @staticmethod
    def _ensure_legacy_schema(db: sqlite3.Connection) -> None:
        """Make the migration CLI safe for both fresh installs and V1 databases."""
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
              display_name TEXT NOT NULL, password_hash TEXT NOT NULL, created_at INTEGER NOT NULL,
              last_login_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              question_id TEXT NOT NULL, selected_json TEXT NOT NULL, correct INTEGER NOT NULL,
              skipped INTEGER NOT NULL DEFAULT 0, topic TEXT NOT NULL, question_type TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS question_state (
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, question_id TEXT NOT NULL,
              last_correct INTEGER, wrong_count INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0,
              last_answer_json TEXT, updated_at INTEGER NOT NULL, PRIMARY KEY (user_id, question_id)
            );
            CREATE TABLE IF NOT EXISTS user_state (
              user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
              active_session_json TEXT, updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL
            );
            """
        )

    @staticmethod
    def _ensure_user_columns(db: sqlite3.Connection) -> None:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        for name, declaration in USER_COLUMNS.items():
            if name not in columns:
                db.execute(f"ALTER TABLE users ADD COLUMN {name} {declaration}")


def load_question_bank(path: str | Path) -> list[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    if not text.startswith(QUESTION_PREFIX):
        raise V2Error("invalid_question_bank", "题库格式无效")
    payload = json.loads(text[len(QUESTION_PREFIX):].rstrip().removesuffix(";"))
    questions = payload.get("questions")
    if not isinstance(questions, list):
        raise V2Error("invalid_question_bank", "题库缺少 questions 数组")
    return questions


def import_question_bank(db_path: str | Path, bank_path: str | Path, actor_admin_id: int | None = None) -> dict[str, int]:
    questions = load_question_bank(bank_path)
    created = revised = unchanged = 0
    now = int(time.time())
    with connect_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            for item in questions:
                question_id = str(item["id"])
                payload = _json(item)
                content_hash = hashlib.sha256(payload.encode()).hexdigest()
                row = db.execute(
                    """SELECT q.current_revision_id,r.content_hash,r.created_by
                       FROM questions q LEFT JOIN question_revisions r ON r.id=q.current_revision_id
                       WHERE q.id=?""",
                    (question_id,),
                ).fetchone()
                if not row:
                    db.execute(
                        """INSERT INTO questions(id, topic, question_type, difficulty, source, status, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, 'published', ?, ?)""",
                        (question_id, item.get("topic", ""), item.get("type", "single"), item.get("difficulty"), item.get("source", ""), now, now),
                    )
                    created += 1
                elif row["created_by"] is not None:
                    # An administrator-created current revision is authoritative.
                    # Startup/deploy imports must never silently roll it back.
                    unchanged += 1
                    continue
                elif row["content_hash"] == content_hash:
                    unchanged += 1
                    continue
                previous = db.execute(
                    "SELECT id FROM question_revisions WHERE question_id = ? AND content_hash = ?",
                    (question_id, content_hash),
                ).fetchone()
                if previous:
                    db.execute(
                        """UPDATE questions SET topic=?,question_type=?,difficulty=?,source=?,current_revision_id=?,updated_at=?
                           WHERE id=?""",
                        (item.get("topic", ""),item.get("type", "single"),item.get("difficulty"),item.get("source", ""),previous["id"],now,question_id),
                    )
                    unchanged += 1
                    continue
                version = db.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM question_revisions WHERE question_id = ?",
                    (question_id,),
                ).fetchone()["version"]
                cursor = db.execute(
                    """INSERT INTO question_revisions(question_id, version, payload_json, content_hash, change_summary, created_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (question_id, version, payload, content_hash, "initial import" if version == 1 else "bank update", actor_admin_id, now),
                )
                db.execute(
                    """UPDATE questions SET topic = ?, question_type = ?, difficulty = ?, source = ?,
                       current_revision_id = ?, updated_at = ? WHERE id = ?""",
                    (item.get("topic", ""), item.get("type", "single"), item.get("difficulty"), item.get("source", ""), cursor.lastrowid, now, question_id),
                )
                revised += 1
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {"total": len(questions), "created": created, "revised": revised, "unchanged": unchanged}


class V2Service:
    def __init__(self, db_path: str | Path, now: Callable[[], int] | None = None):
        self.db_path = Path(db_path)
        self.now = now or (lambda: int(time.time()))

    def _connect(self) -> sqlite3.Connection:
        return connect_db(self.db_path)

    def create_admin_account(self, username: str, password: str, roles: Iterable[str], display_name: str = "", actor_admin_id: int | None = None) -> int:
        requested = set(roles)
        if not requested or not requested.issubset(ROLE_PERMISSIONS):
            raise V2Error("invalid_role", "管理员角色无效")
        username = str(username).strip()
        display_name = str(display_name).strip() or username
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,24}", username):
            raise V2Error("invalid_username", "管理员账号需为3至24位字母、数字、点、下划线或连字符")
        if not 1 <= len(display_name) <= 30:
            raise V2Error("invalid_display_name", "管理员显示名称需为1至30个字符")
        if not 12 <= len(password) <= 128:
            raise V2Error("invalid_password", "管理员密码需为12至128位")
        with self._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM admin_accounts").fetchone()[0]
            if count and not self.has_permission(actor_admin_id, "roles.manage", db):
                raise V2Error("forbidden", "无权创建管理员")
            cursor = db.execute(
                """INSERT INTO admin_accounts(username, display_name, password_hash, created_at)
                   VALUES (?, ?, ?, ?)""",
                (username, display_name or username, encode_password(password), self.now()),
            )
            admin_id = int(cursor.lastrowid)
            db.executemany("INSERT INTO admin_account_roles(admin_id, role_name) VALUES (?, ?)", [(admin_id, role) for role in sorted(requested)])
            self._audit(db, actor_admin_id, "admin.create", "admin", str(admin_id), True, None, {"username": username, "roles": sorted(requested)})
        return admin_id

    def has_permission(self, admin_id: int | None, permission: str, db: sqlite3.Connection | None = None) -> bool:
        if admin_id is None:
            return False
        owns_connection = db is None
        db = db or self._connect()
        try:
            admin = db.execute("SELECT status FROM admin_accounts WHERE id = ?", (admin_id,)).fetchone()
            if not admin or admin["status"] != "active":
                return False
            roles = [row["role_name"] for row in db.execute("SELECT role_name FROM admin_account_roles WHERE admin_id = ?", (admin_id,))]
            return any("*" in ROLE_PERMISSIONS.get(role, set()) or permission in ROLE_PERMISSIONS.get(role, set()) for role in roles)
        finally:
            if owns_connection:
                db.close()

    def require_permission(self, admin_id: int | None, permission: str, db: sqlite3.Connection | None = None) -> None:
        if not self.has_permission(admin_id, permission, db):
            raise V2Error("forbidden", "权限不足")

    def _audit(self, db: sqlite3.Connection, actor: int | None, action: str, target_type: str, target_id: str | None,
               success: bool, before: Any, after: Any, request_id: str | None = None) -> None:
        db.execute(
            """INSERT INTO admin_audit_logs(actor_admin_id, action, target_type, target_id, success,
               before_json, after_json, request_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (actor, action, target_type, target_id, int(success), _json(_clean_audit(before)) if before is not None else None,
             _json(_clean_audit(after)) if after is not None else None, request_id or uuid.uuid4().hex, self.now()),
        )

    def create_registration_code(self, actor_admin_id: int, code: str | None = None, label: str = "", expires_at: int | None = None,
                                 max_uses: int | None = None) -> dict[str, Any]:
        self.require_permission(actor_admin_id, "registration_codes.manage")
        plain = code or ("NF-" + secrets.token_hex(6).upper())
        if not 6 <= len(plain) <= 64 or any(char.isspace() for char in plain):
            raise V2Error("invalid_code", "注册码格式无效")
        label = str(label).strip()
        if len(label) > 80:
            raise V2Error("invalid_code", "注册码备注不能超过80个字符")
        if expires_at is not None:
            expires_at = int(expires_at)
            if expires_at <= self.now():
                raise V2Error("invalid_code", "注册码失效时间必须晚于当前时间")
        if max_uses is not None:
            max_uses = int(max_uses)
            if max_uses < 1:
                raise V2Error("invalid_code", "注册码最大使用次数必须大于0")
        digest = hashlib.sha256(plain.encode()).hexdigest()
        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO registration_codes(code_hash, code_prefix, label, expires_at, max_uses, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (digest, plain[:6], label, expires_at, max_uses, actor_admin_id, self.now()),
            )
            code_id = int(cursor.lastrowid)
            self._audit(db, actor_admin_id, "registration_code.create", "registration_code", str(code_id), True, None,
                        {"prefix": plain[:6], "expiresAt": expires_at, "maxUses": max_uses})
        return {"id": code_id, "code": plain}

    def consume_registration_code(self, code: str, user_id: int) -> int:
        digest, now = hashlib.sha256(code.encode()).hexdigest(), self.now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM registration_codes WHERE code_hash = ?", (digest,)).fetchone()
                if not row or row["revoked_at"] is not None or (row["expires_at"] is not None and row["expires_at"] <= now):
                    raise V2Error("invalid_registration_code", "注册码无效或已过期")
                if row["max_uses"] is not None and row["used_count"] >= row["max_uses"]:
                    raise V2Error("registration_code_exhausted", "注册码使用次数已达上限")
                db.execute("INSERT INTO registration_code_uses(code_id, user_id, used_at) VALUES (?, ?, ?)", (row["id"], user_id, now))
                db.execute("UPDATE registration_codes SET used_count = used_count + 1 WHERE id = ?", (row["id"],))
                db.commit()
                return int(row["id"])
            except Exception:
                db.rollback()
                raise

    def create_practice_session(self, user_id: int, question_ids: list[str], mode: str = "practice", topic: str = "") -> dict[str, Any]:
        if mode not in {"practice", "memorize"} or not question_ids:
            raise V2Error("invalid_practice", "练习模式或题目列表无效")
        session_id, now = uuid.uuid4().hex, self.now()
        with self._connect() as db:
            self._require_active_user(db, user_id)
            found = db.execute(
                f"SELECT COUNT(*) FROM questions WHERE status = 'published' AND deleted_at IS NULL AND id IN ({','.join('?' for _ in question_ids)})",
                question_ids,
            ).fetchone()[0]
            if found != len(set(question_ids)):
                raise V2Error("question_not_found", "题目不存在或未发布")
            db.execute(
                """INSERT INTO practice_sessions(id, user_id, mode, topic, question_ids_json, started_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, user_id, mode, topic, _json(question_ids), now, now),
            )
        return {"id": session_id, "mode": mode, "questionIds": question_ids}

    def list_practice_sessions(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM practice_sessions WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def submit_practice_answer(self, user_id: int, session_id: str, question_id: str, selected: list[str], submission_id: str,
                               duration_seconds: int = 0) -> dict[str, Any]:
        if not submission_id or not isinstance(selected, list):
            raise V2Error("invalid_answer", "submissionId 和答案不能为空")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                session = db.execute("SELECT * FROM practice_sessions WHERE id = ? AND user_id = ?", (session_id, user_id)).fetchone()
                if not session:
                    raise V2Error("practice_not_found", "练习不存在")
                if session['mode']!='practice':
                    raise V2Error('invalid_practice','背题模式只查看解析，不提交作答成绩')
                prior = db.execute("SELECT * FROM practice_answers WHERE session_id = ? AND submission_id = ?", (session_id, submission_id)).fetchone()
                if prior:
                    db.rollback()
                    return self._practice_result(prior)
                if question_id not in json.loads(session["question_ids_json"]):
                    raise V2Error("question_not_in_session", "题目不属于当前练习")
                revision = db.execute(
                    """SELECT r.* FROM questions q JOIN question_revisions r ON r.id = q.current_revision_id
                       WHERE q.id = ? AND q.status = 'published' AND q.deleted_at IS NULL""",
                    (question_id,),
                ).fetchone()
                if not revision:
                    raise V2Error("question_not_found", "题目不存在")
                payload = json.loads(revision["payload_json"])
                valid = {str(option["key"]) for option in payload.get("options", [])}
                normalized = sorted(set(map(str, selected)))
                if not set(normalized).issubset(valid):
                    raise V2Error("invalid_option", "答案包含无效选项")
                correct = normalized == sorted(map(str, payload.get("answer", [])))
                now = self.now()
                try:
                    cursor = db.execute(
                        """INSERT INTO practice_answers(session_id, user_id, question_id, revision_id, submission_id,
                           selected_json, correct, duration_seconds, answered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (session_id, user_id, question_id, revision["id"], submission_id, _json(normalized), int(correct),
                         max(0, min(int(duration_seconds), 86400)), now),
                    )
                except sqlite3.IntegrityError:
                    prior = db.execute("SELECT * FROM practice_answers WHERE session_id = ? AND question_id = ?", (session_id, question_id)).fetchone()
                    db.rollback()
                    if prior:
                        return self._practice_result(prior)
                    raise
                db.execute("UPDATE practice_sessions SET updated_at = ? WHERE id = ?", (now, session_id))
                db.execute(
                    """INSERT INTO question_state(user_id, question_id, last_correct, wrong_count, favorite, last_answer_json, updated_at)
                       VALUES (?, ?, ?, ?, 0, ?, ?)
                       ON CONFLICT(user_id, question_id) DO UPDATE SET
                         last_correct = excluded.last_correct,
                         wrong_count = question_state.wrong_count + CASE WHEN excluded.last_correct = 0 THEN 1 ELSE 0 END,
                         last_answer_json = excluded.last_answer_json,
                         updated_at = excluded.updated_at""",
                    (user_id, question_id, int(correct), 0 if correct else 1, _json(normalized), now),
                )
                row = db.execute("SELECT * FROM practice_answers WHERE id = ?", (cursor.lastrowid,)).fetchone()
                db.commit()
                return self._practice_result(row)
            except Exception:
                db.rollback()
                raise

    def _practice_result(self, answer_row: sqlite3.Row) -> dict[str, Any]:
        with self._connect() as db:
            revision = db.execute("SELECT payload_json FROM question_revisions WHERE id = ?", (answer_row["revision_id"],)).fetchone()
        payload = json.loads(revision["payload_json"])
        return {
            "answerId": answer_row["id"], "submissionId": answer_row["submission_id"],
            "correct": bool(answer_row["correct"]), "answer": payload.get("answer", []),
            "explanation": payload.get("explanation", ""), "source": payload.get("source", ""),
            "references": payload.get("references", []),
            "explanationConfidence": payload.get("explanationConfidence", "medium"),
            "needsReview": bool(payload.get("needsReview")),
        }

    def create_feedback(self, user_id: int, question_id: str, feedback_type: str, description: str = "") -> int:
        allowed = {"answer", "explanation", "question", "image", "other"}
        if feedback_type not in allowed or len(description) > 2000:
            raise V2Error("invalid_feedback", "反馈类型或说明无效")
        with self._connect() as db:
            self._require_active_user(db, user_id)
            if not db.execute("SELECT 1 FROM questions WHERE id = ?", (question_id,)).fetchone():
                raise V2Error("question_not_found", "题目不存在")
            now = self.now()
            cursor = db.execute(
                """INSERT INTO question_feedback(user_id, question_id, feedback_type, description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, question_id, feedback_type, description.strip(), now, now),
            )
        return int(cursor.lastrowid)

    def revise_question(self, actor_admin_id: int, question_id: str, payload: dict[str, Any], summary: str,
                        status: str | None = None) -> int:
        self.require_permission(actor_admin_id, "questions.write")
        content = _json(payload)
        digest = hashlib.sha256(content.encode()).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                question = db.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
                if not question:
                    raise V2Error("question_not_found", "题目不存在")
                next_status = status or question["status"]
                if next_status not in {"draft", "published", "unpublished"}:
                    raise V2Error("invalid_status", "题目状态无效")
                version = db.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM question_revisions WHERE question_id = ?", (question_id,)).fetchone()[0]
                cursor = db.execute(
                    """INSERT INTO question_revisions(question_id, version, payload_json, content_hash, change_summary, created_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (question_id, version, content, digest, summary, actor_admin_id, self.now()),
                )
                db.execute(
                    """UPDATE questions SET current_revision_id = ?, topic = ?, question_type = ?, difficulty = ?,
                       source = ?, status = ?, updated_at = ? WHERE id = ?""",
                    (cursor.lastrowid, payload.get("topic", "未分类"), payload["type"], payload.get("difficulty"),
                     payload.get("source", ""), next_status, self.now(), question_id),
                )
                self._audit(db, actor_admin_id, "question.revise", "question", question_id, True,
                            {"revisionId": question["current_revision_id"]}, {"revisionId": cursor.lastrowid, "summary": summary})
                db.commit()
                return int(cursor.lastrowid)
            except Exception:
                db.rollback()
                raise

    def create_exam_paper(self, actor_admin_id: int, title: str, question_ids: list[str], points: float = 1,
                          duration_seconds: int = 3600, pass_score: float = 60, published: bool = False) -> str:
        self.require_permission(actor_admin_id, "papers.write")
        if not question_ids or points <= 0 or duration_seconds <= 0:
            raise V2Error("invalid_paper", "试卷参数无效")
        paper_id, now = uuid.uuid4().hex, self.now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """INSERT INTO exam_papers(id, title, duration_seconds, pass_score, status, created_by, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (paper_id, title, duration_seconds, pass_score, "published" if published else "draft", actor_admin_id, now, now),
                )
                for position, question_id in enumerate(question_ids, 1):
                    row = db.execute(
                        """SELECT q.current_revision_id, r.payload_json FROM questions q
                           JOIN question_revisions r ON r.id = q.current_revision_id WHERE q.id = ?""",
                        (question_id,),
                    ).fetchone()
                    if not row:
                        raise V2Error("question_not_found", f"题目 {question_id} 不存在")
                    db.execute(
                        """INSERT INTO exam_paper_items(paper_id, position, question_id, revision_id, snapshot_json, points)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (paper_id, position, question_id, row["current_revision_id"], row["payload_json"], points),
                    )
                self._audit(db, actor_admin_id, "paper.create", "exam_paper", paper_id, True, None,
                            {"title": title, "questionCount": len(question_ids), "status": "published" if published else "draft"})
                db.commit()
            except Exception:
                db.rollback()
                raise
        return paper_id

    def start_exam_attempt(self, user_id: int, paper_id: str) -> str:
        attempt_id, now = uuid.uuid4().hex, self.now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._require_active_user(db, user_id)
                paper = db.execute("SELECT * FROM exam_papers WHERE id = ? AND status = 'published' AND deleted_at IS NULL", (paper_id,)).fetchone()
                if not paper:
                    raise V2Error("paper_not_found", "试卷不存在或未发布")
                db.execute(
                    """INSERT INTO exam_attempts(id, paper_id, user_id, started_at, deadline_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (attempt_id, paper_id, user_id, now, now + paper["duration_seconds"]),
                )
                db.execute(
                    """INSERT INTO exam_attempt_items(attempt_id, position, question_id, revision_id, snapshot_json, points, grading_rule)
                       SELECT ?, position, question_id, revision_id, snapshot_json, points, grading_rule
                       FROM exam_paper_items WHERE paper_id = ?""",
                    (attempt_id, paper_id),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return attempt_id

    def get_exam_snapshot(self, user_id: int, attempt_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM exam_attempts WHERE id = ? AND user_id = ?", (attempt_id, user_id)).fetchone():
                raise V2Error("attempt_not_found", "考试记录不存在")
            rows = db.execute("SELECT * FROM exam_attempt_items WHERE attempt_id = ? ORDER BY position", (attempt_id,)).fetchall()
        return [{**dict(row), "snapshot": json.loads(row["snapshot_json"])} for row in rows]

    def create_content(self, actor_admin_id: int, content_type: str, title: str, body_text: str, source: str = "",
                       published: bool = False, status: str | None = None) -> str:
        self.require_permission(actor_admin_id, "content.write")
        if content_type not in {"article", "announcement", "changelog"}:
            raise V2Error("invalid_content", "内容类型无效")
        content_status = status or ("published" if published else "draft")
        if content_status not in {"draft", "published", "unpublished"}:
            raise V2Error("invalid_status", "内容状态无效")
        content_id, now = uuid.uuid4().hex, self.now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO content_articles(id, content_type, title, body_text, source, status, created_by, published_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (content_id, content_type, title, body_text, source, content_status, actor_admin_id,
                 now if content_status == "published" else None, now, now),
            )
            self._audit(db, actor_admin_id, "content.create", "content", content_id, True, None,
                        {"type": content_type, "title": title, "status": content_status})
        return content_id

    @staticmethod
    def _require_active_user(db: sqlite3.Connection, user_id: int) -> None:
        row = db.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or row["status"] != "active":
            raise V2Error("user_unavailable", "用户不存在或已停用")


def _quick_check(path: str | Path) -> str:
    connection = sqlite3.connect(str(path))
    try:
        return str(connection.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        connection.close()


def backup_database(db_path: str | Path, destination_dir: str | Path, prefix: str = "netforge") -> dict[str, Any]:
    source_path, destination = Path(db_path), Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    final_path = destination / f"{prefix}-{timestamp}-{secrets.token_hex(3)}.db"
    fd, temp_name = tempfile.mkstemp(prefix=".backup-", suffix=".db", dir=destination)
    os.close(fd)
    temp_path = Path(temp_name)
    restore_fd, restore_name = tempfile.mkstemp(prefix="netforge-restore-probe-", suffix=".db")
    os.close(restore_fd)
    restore_probe = Path(restore_name)
    try:
        source = connect_db(source_path)
        target = sqlite3.connect(str(temp_path))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        check = _quick_check(temp_path)
        if check != "ok":
            raise V2Error("backup_invalid", f"备份 quick_check 失败：{check}")
        shutil.copy2(temp_path, restore_probe)
        restored_check = _quick_check(restore_probe)
        if restored_check != "ok":
            raise V2Error("restore_verification_failed", f"隔离恢复验证失败：{restored_check}")
        os.replace(temp_path, final_path)
        digest = hashlib.sha256(final_path.read_bytes()).hexdigest()
        size = final_path.stat().st_size
        try:
            with connect_db(source_path) as db:
                db.execute(
                    """INSERT INTO backup_metadata(file_name, sha256, size_bytes, quick_check, restore_verified, created_at)
                       VALUES (?, ?, ?, ?, 1, ?)""",
                    (final_path.name, digest, size, check, int(time.time())),
                )
        except sqlite3.OperationalError:
            pass
        return {"path": str(final_path), "sha256": digest, "sizeBytes": size, "quickCheck": check, "restoreVerified": True}
    finally:
        temp_path.unlink(missing_ok=True)
        restore_probe.unlink(missing_ok=True)


def restore_database_verified(backup_path: str | Path, destination_path: str | Path, *, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        raise V2Error("confirmation_required", "恢复数据库必须显式确认")
    if _quick_check(backup_path) != "ok":
        raise V2Error("backup_invalid", "备份文件校验失败")
    destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".restore.tmp")
    shutil.copy2(backup_path, temp)
    if _quick_check(temp) != "ok":
        temp.unlink(missing_ok=True)
        raise V2Error("restore_verification_failed", "恢复副本校验失败")
    os.replace(temp, destination)
    return {"path": str(destination), "quickCheck": "ok"}
