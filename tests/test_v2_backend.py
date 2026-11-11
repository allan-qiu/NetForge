from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from netforge_v2 import (
    MigrationManager,
    V2Error,
    V2Service,
    backup_database,
    connect_db,
    import_question_bank,
    restore_database_verified,
)


def create_legacy_database(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            last_login_at INTEGER
        );
        CREATE TABLE sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            question_id TEXT NOT NULL,
            selected_json TEXT NOT NULL,
            correct INTEGER NOT NULL,
            skipped INTEGER NOT NULL DEFAULT 0,
            topic TEXT NOT NULL,
            question_type TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE question_state (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            question_id TEXT NOT NULL,
            last_correct INTEGER,
            wrong_count INTEGER NOT NULL DEFAULT 0,
            favorite INTEGER NOT NULL DEFAULT 0,
            last_answer_json TEXT,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, question_id)
        );
        CREATE TABLE user_state (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            active_session_json TEXT,
            updated_at INTEGER NOT NULL
        );
        """
    )
    db.executemany(
        "INSERT INTO users(username, display_name, password_hash, created_at) VALUES (?, ?, 'legacy', 1)",
        [("alice", "Alice"), ("bob", "Bob")],
    )
    db.commit()
    db.close()


def write_bank(path: Path) -> None:
    payload = {
        "questions": [
            {
                "id": "net-test-1", "topic": "WLAN", "type": "single", "question": "Q1",
                "options": [{"key": "A", "text": "one"}, {"key": "B", "text": "two"}],
                "answer": ["A"], "explanation": "because one", "source": "test", "needsReview": False,
            },
            {
                "id": "net-test-2", "topic": "路由交换", "type": "multiple", "question": "Q2",
                "options": [{"key": "A", "text": "one"}, {"key": "B", "text": "two"}],
                "answer": ["A", "B"], "explanation": "both", "source": "test", "needsReview": False,
            },
        ]
    }
    path.write_text("window.NETWORK_QUESTION_BANK = " + json.dumps(payload, ensure_ascii=False) + ";", encoding="utf-8")


class V2BackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "netforge.db"
        self.bank_path = self.root / "bank.js"
        create_legacy_database(self.db_path)
        write_bank(self.bank_path)
        self.manager = MigrationManager(self.db_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def migrate_and_import(self) -> V2Service:
        self.manager.apply()
        import_question_bank(self.db_path, self.bank_path)
        return V2Service(self.db_path, now=lambda: 1_800_000_000)

    def test_migrations_and_question_import_are_idempotent(self) -> None:
        first = self.manager.apply()
        second = self.manager.apply()
        self.assertEqual(first, ["001_v2_core", "002_v2_indexes", "003_learning_upgrade", "004_user_admin_edits"])
        self.assertEqual(second, [])
        with connect_db(self.db_path) as raw_db:
            columns = {row[1] for row in raw_db.execute("PRAGMA table_info(users)")}
        self.assertTrue({"status", "deleted_at", "leaderboard_opt_in", "updated_at"}.issubset(columns))
        initial = import_question_bank(self.db_path, self.bank_path)
        repeated = import_question_bank(self.db_path, self.bank_path)
        self.assertEqual(initial["created"], 2)
        self.assertEqual(initial["revised"], 2)
        self.assertEqual(repeated["revised"], 0)
        with connect_db(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM question_revisions").fetchone()[0], 2)

    def test_two_users_are_isolated_and_answers_are_idempotent(self) -> None:
        service = self.migrate_and_import()
        session = service.create_practice_session(1, ["net-test-1"])
        self.assertEqual(service.list_practice_sessions(2), [])
        first = service.submit_practice_answer(1, session["id"], "net-test-1", ["A"], "submission-1")
        second = service.submit_practice_answer(1, session["id"], "net-test-1", ["B"], "submission-1")
        self.assertEqual(first["answerId"], second["answerId"])
        self.assertTrue(first["correct"])
        with connect_db(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM practice_answers").fetchone()[0], 1)
        with self.assertRaises(V2Error) as caught:
            service.submit_practice_answer(2, session["id"], "net-test-1", ["A"], "submission-2")
        self.assertEqual(caught.exception.code, "practice_not_found")

    def test_rbac_enforces_question_and_support_boundaries(self) -> None:
        service = self.migrate_and_import()
        super_id = service.create_admin_account("root-admin", "correct horse battery", ["super_admin"])
        question_id = service.create_admin_account("question-admin", "correct horse battery", ["question_admin"], actor_admin_id=super_id)
        support_id = service.create_admin_account("support-admin", "correct horse battery", ["support_admin"], actor_admin_id=super_id)
        self.assertTrue(service.has_permission(question_id, "questions.write"))
        self.assertFalse(service.has_permission(question_id, "users.status"))
        self.assertTrue(service.has_permission(support_id, "users.read"))
        self.assertFalse(service.has_permission(support_id, "users.status"))
        self.assertFalse(service.has_permission(support_id, "questions.write"))
        with self.assertRaises(V2Error):
            service.revise_question(support_id, "net-test-1", {"id": "net-test-1"}, "blocked")
        with connect_db(self.db_path) as db:
            audit = db.execute("SELECT after_json FROM admin_audit_logs WHERE action = 'admin.create' ORDER BY id LIMIT 1").fetchone()
        self.assertNotIn("correct horse battery", audit["after_json"])

    def test_exam_attempt_keeps_question_snapshot_after_revision(self) -> None:
        service = self.migrate_and_import()
        admin_id = service.create_admin_account("root-admin", "correct horse battery", ["super_admin"])
        paper_id = service.create_exam_paper(admin_id, "Mock", ["net-test-1"], published=True)
        attempt_id = service.start_exam_attempt(1, paper_id)
        before = service.get_exam_snapshot(1, attempt_id)[0]["snapshot"]
        changed = dict(before)
        changed["answer"] = ["B"]
        changed["explanation"] = "changed"
        service.revise_question(admin_id, "net-test-1", changed, "test revision")
        after = service.get_exam_snapshot(1, attempt_id)[0]["snapshot"]
        self.assertEqual(before["answer"], ["A"])
        self.assertEqual(after["answer"], ["A"])

    def test_question_revision_updates_search_metadata_and_status_atomically(self) -> None:
        service = self.migrate_and_import()
        admin_id = service.create_admin_account("root-admin", "correct horse battery", ["super_admin"])
        changed = {
            "id": "net-test-1", "topic": "网络安全", "type": "judge", "difficulty": "hard",
            "question": "Updated", "options": [{"key": "A", "text": "yes"}, {"key": "B", "text": "no"}],
            "answer": ["B"], "explanation": "updated explanation", "source": "updated source",
        }
        service.revise_question(admin_id, "net-test-1", changed, "metadata update", status="unpublished")
        with connect_db(self.db_path) as db:
            row = db.execute("SELECT topic, question_type, difficulty, source, status FROM questions WHERE id='net-test-1'").fetchone()
            revision = db.execute("SELECT MAX(version) FROM question_revisions WHERE question_id='net-test-1'").fetchone()[0]
        self.assertEqual(dict(row), {"topic": "网络安全", "question_type": "judge", "difficulty": "hard", "source": "updated source", "status": "unpublished"})
        self.assertEqual(revision, 2)

    def test_startup_import_never_rolls_back_an_admin_revision(self) -> None:
        service = self.migrate_and_import()
        admin_id = service.create_admin_account("root-admin", "correct horse battery", ["super_admin"])
        with connect_db(self.db_path) as db:
            original = json.loads(db.execute("SELECT payload_json FROM question_revisions WHERE question_id='net-test-1'").fetchone()[0])
        changed = {**original, "question": "Administrator edit", "answer": ["B"], "explanation": "Administrator explanation"}
        admin_revision = service.revise_question(admin_id, "net-test-1", changed, "admin edit")
        result = import_question_bank(self.db_path, self.bank_path)
        with connect_db(self.db_path) as db:
            row = db.execute("SELECT current_revision_id FROM questions WHERE id='net-test-1'").fetchone()
            current = json.loads(db.execute("SELECT payload_json FROM question_revisions WHERE id=?", (row["current_revision_id"],)).fetchone()[0])
            revision_count = db.execute("SELECT COUNT(*) FROM question_revisions WHERE question_id='net-test-1'").fetchone()[0]
        self.assertEqual(result["revised"], 0)
        self.assertEqual(row["current_revision_id"], admin_revision)
        self.assertEqual(current["question"], "Administrator edit")
        self.assertEqual(revision_count, 2)

    def test_backup_is_consistent_and_restores_in_isolation(self) -> None:
        self.migrate_and_import()
        backup = backup_database(self.db_path, self.root / "backups")
        self.assertEqual(backup["quickCheck"], "ok")
        self.assertTrue(backup["restoreVerified"])
        with connect_db(self.db_path) as db:
            db.execute("INSERT INTO users(username, display_name, password_hash, created_at) VALUES ('later', 'Later', 'x', 2)")
        restored = self.root / "restored.db"
        restore_database_verified(backup["path"], restored, confirm=True)
        with connect_db(restored) as db:
            names = {row[0] for row in db.execute("SELECT username FROM users")}
        self.assertEqual(names, {"alice", "bob"})
        with self.assertRaises(V2Error):
            restore_database_verified(backup["path"], self.root / "blocked.db", confirm=False)


if __name__ == "__main__":
    unittest.main()
