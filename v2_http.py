"""HTTP adapter for NetForge V2.

The legacy ``AppHandler`` can call ``configure`` once at startup, then delegate
``/api/v2/*`` requests to the matching ``handle_*`` function.  The adapter uses
only Python's standard library and keeps legacy routes intact.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
import zipfile
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET
import learning_features as features

from netforge_v2 import (
    MigrationManager,
    ROLE_PERMISSIONS,
    V2Error,
    V2Service,
    backup_database,
    connect_db,
    encode_password,
    import_question_bank,
    verify_password,
)


@dataclass
class Runtime:
    db_path: Path
    bank_path: Path
    backup_dir: Path
    service: V2Service
    bootstrap_admin_id: int


_runtime: Runtime | None = None
_IMPORT_MAX = 10 * 1024 * 1024
_AVATAR_MAX = 2 * 1024 * 1024
_PAGE_MAX = 200
_DEFAULT_CHANGELOGS = (
    ("system-changelog-2.1.2", "V2.1.2", "修复题库新增与编辑流程，保护管理员修订内容，并完善多角色权限合并。"),
    ("system-changelog-2.1.3", "V2.1.3", "首页开始练习改为直接连续刷题；排行榜改为所有正常用户参加并兼容旧版答题记录。"),
    ("system-changelog-2.1.4", "V2.1.4", "修复纠错弹窗和后台编辑器遮罩问题，增加排行榜用户简介、本地头像上传并优化模拟考试布局。"),
    ("system-changelog-2.2.0", "V2.2.0", "统一用户端与管理端设计系统，优化深浅主题、移动导航、交互状态与无障碍体验。"),
    ("system-changelog-2.3.0", "V2.3.0", "新增账号独立外观、六套配色、AI题目讲解与校订参考文档、用户详情与AI学习画像、硬删除与记录删除、错题本重练、动态寄语和练习/模考双分数排行榜。"),
    ("system-changelog-2.3.1", "V2.3.1", "修复AI讲解和参考文档退出，支持固定关闭入口、Esc及取消等待；新增超级管理员用户资料编辑、统计校正与密码修改，并保护只读字段和登录会话。"),
    ("system-changelog-2.4.0", "V2.4.0", "背题支持20/60/80题及不限题数、AI解析和首页一键入口；新增本人专属AI总体学习分析；练习分调整为正确次数÷（错误次数＋1）×10，日周榜按北京时间自然日与自然周统计。"),
)


def configure(db_path: str | Path, bank_path: str | Path, admin_username: str = "admin",
              admin_password: str = "", backup_dir: str | Path | None = None) -> Runtime:
    """Apply additive migrations, import questions, and bootstrap the legacy admin."""
    global _runtime
    db_path, bank_path = Path(db_path), Path(bank_path)
    MigrationManager(db_path).apply()
    import_question_bank(db_path, bank_path)
    service = V2Service(db_path)
    with connect_db(db_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
              token_hash TEXT NOT NULL UNIQUE, expires_at INTEGER NOT NULL, used_at INTEGER,
              created_by INTEGER REFERENCES admin_accounts(id), created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS question_import_batches (
              id TEXT PRIMARY KEY, actor_admin_id INTEGER REFERENCES admin_accounts(id),
              file_hash TEXT NOT NULL, rows_json TEXT NOT NULL, errors_json TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'preview', created_at INTEGER NOT NULL, committed_at INTEGER
            );
            """
        )
        admin = db.execute("SELECT id FROM admin_accounts WHERE username = ? COLLATE NOCASE", (admin_username,)).fetchone()
    if admin:
        admin_id = int(admin["id"])
    else:
        admin_id = service.create_admin_account(admin_username, admin_password or secrets.token_urlsafe(24), ["super_admin"])
    now = int(time.time())
    with connect_db(db_path) as db:
        for offset, (content_id, title, body_text) in enumerate(_DEFAULT_CHANGELOGS):
            published_at = now - (len(_DEFAULT_CHANGELOGS) - offset) * 60
            db.execute(
                """INSERT OR IGNORE INTO content_articles
                   (id,content_type,title,body_text,source,status,created_by,published_at,created_at,updated_at)
                   VALUES (?,'changelog',?,?,?,'published',NULL,?,?,?)""",
                (content_id, title, body_text, "NetForge", published_at, published_at, published_at),
            )
    _runtime = Runtime(db_path, bank_path, Path(backup_dir or db_path.parent / "backups"), service, admin_id)
    return _runtime


def _rt() -> Runtime:
    if _runtime is None:
        raise RuntimeError("v2_http.configure() has not been called")
    return _runtime


def _request_id(handler: Any) -> str:
    return getattr(handler, "_v2_request_id", None) or uuid.uuid4().hex


def _success(handler: Any, data: Any, status: int = HTTPStatus.OK) -> None:
    handler._json(status, {"ok": True, "data": data, "requestId": _request_id(handler)})


def _failure(handler: Any, status: int, code: str, message: str) -> None:
    handler._json(status, {"ok": False, "error": {"code": code, "message": message}, "requestId": _request_id(handler)})


def _dispatch(handler: Any, callback) -> bool:
    try:
        callback()
    except V2Error as exc:
        status = {
            "user_edit_conflict": HTTPStatus.CONFLICT, "username_conflict": HTTPStatus.CONFLICT,
            "ai_unavailable": HTTPStatus.SERVICE_UNAVAILABLE, "ai_provider_error": HTTPStatus.BAD_GATEWAY,
            "ai_busy": HTTPStatus.CONFLICT, "ai_rate_limit": HTTPStatus.TOO_MANY_REQUESTS,
            "forbidden": HTTPStatus.FORBIDDEN, "user_unavailable": HTTPStatus.FORBIDDEN,
            "question_not_found": HTTPStatus.NOT_FOUND, "practice_not_found": HTTPStatus.NOT_FOUND,
            "user_not_found": HTTPStatus.NOT_FOUND,
            "paper_not_found": HTTPStatus.NOT_FOUND, "attempt_not_found": HTTPStatus.NOT_FOUND,
            "registration_code_not_found": HTTPStatus.NOT_FOUND, "feedback_not_found": HTTPStatus.NOT_FOUND,
            "content_not_found": HTTPStatus.NOT_FOUND,
        }.get(exc.code, HTTPStatus.BAD_REQUEST)
        _failure(handler, status, exc.code, exc.message)
    except (ValueError, TypeError, json.JSONDecodeError):
        _failure(handler, HTTPStatus.BAD_REQUEST, "invalid_request", "请求参数无效")
    except sqlite3.IntegrityError:
        _failure(handler, HTTPStatus.CONFLICT, "conflict", "数据已存在或状态冲突")
    return True


def _user(handler: Any) -> dict[str, Any] | sqlite3.Row | None:
    return handler._require_user()


def _user_id(user: Any) -> int:
    return int(user["id"])


def _admin_id(handler: Any, permission: str) -> int | None:
    if not handler._require_admin():
        return None
    admin_id = int(getattr(handler, "v2_admin_id", _rt().bootstrap_admin_id))
    _rt().service.require_permission(admin_id, permission)
    return admin_id


def _q(query: dict[str, list[str]], name: str, default: str = "") -> str:
    value = query.get(name, [default])
    return str(value[0] if isinstance(value, list) else value)


def _paging(query: dict[str, list[str]]) -> tuple[int, int, int]:
    page = max(1, int(_q(query, "page", "1")))
    page_size = min(_PAGE_MAX, max(1, int(_q(query, "pageSize", "20"))))
    return page, page_size, (page - 1) * page_size


def _body(handler: Any) -> dict[str, Any]:
    value = handler._body()
    if not isinstance(value, dict):
        raise V2Error("invalid_request", "请求体必须是对象")
    return value


def _question_public(payload: dict[str, Any], *, include_source: bool = True) -> dict[str, Any]:
    result = {key: payload.get(key) for key in ("id", "topic", "type", "question", "options", "difficulty", "needsReview")}
    if include_source:
        result["source"] = payload.get("source", "")
    return result


def _question_row_payload(db: sqlite3.Connection, question_id: str) -> tuple[sqlite3.Row, dict[str, Any]]:
    row = db.execute(
        """SELECT q.*, r.payload_json, r.version FROM questions q JOIN question_revisions r ON r.id=q.current_revision_id
           WHERE q.id=? AND q.deleted_at IS NULL""", (question_id,),
    ).fetchone()
    if not row:
        raise V2Error("question_not_found", "题目不存在")
    return row, json.loads(row["payload_json"])


def handle_get(handler: Any, path: str, query: dict[str, list[str]]) -> bool:
    if not path.startswith("/api/v2/"):
        return False

    def run() -> None:
        if path.startswith("/api/v2/admin/"):
            return _admin_get(handler, path, query)
        user = _user(handler)
        if not user:
            return
        uid = _user_id(user)
        if path == "/api/v2/home":
            _success(handler, _home(uid))
        elif path == "/api/v2/me/appearance":
            _success(handler, features.preferences(_rt(), 'user', uid))
        elif path == "/api/v2/me/motivation":
            _success(handler, features.motivation(_rt(), uid))
        elif path == "/api/v2/me/ai-summary":
            _success(handler, features.personal_summary(_rt(), uid))
        elif path == "/api/v2/questions":
            _success(handler, _questions(uid, query))
        elif re.fullmatch(r"/api/v2/questions/[^/]+", path):
            question_id = path.rsplit("/", 1)[1]
            with connect_db(_rt().db_path) as db:
                row, payload = _question_row_payload(db, question_id)
                if row["status"] != "published":
                    raise V2Error("question_not_found", "题目不存在")
            _success(handler, {"question": _question_public(payload)})
        elif re.fullmatch(r"/api/v2/practice-sessions/[^/]+", path):
            _success(handler, _practice_detail(uid, path.rsplit("/", 1)[1]))
        elif path == "/api/v2/me/feedback":
            _success(handler, _my_feedback(uid, query))
        elif path == "/api/v2/me/learning":
            _success(handler, _learning(uid))
        elif path == "/api/v2/me/heatmap":
            _success(handler, _heatmap(uid))
        elif path == "/api/v2/me/profile":
            _success(handler, _profile(uid))
        elif path == "/api/v2/leaderboard":
            _success(handler, _leaderboard(uid, _q(query, "period", "week")))
        elif re.fullmatch(r"/api/v2/users/\d+/profile", path):
            _success(handler, _public_profile(int(path.split("/")[-2])))
        elif path == "/api/v2/exam-papers":
            _success(handler, _exam_papers(uid))
        elif re.fullmatch(r"/api/v2/exam-attempts/[^/]+/report", path):
            _success(handler, _exam_report(uid, path.split("/")[-2]))
        elif re.fullmatch(r"/api/v2/exam-attempts/[^/]+", path):
            _success(handler, _exam_attempt(uid, path.rsplit("/", 1)[1]))
        elif path == "/api/v2/content":
            _success(handler, _content_public(_q(query, "type", "announcement"), query))
        else:
            _failure(handler, HTTPStatus.NOT_FOUND, "not_found", "接口不存在")

    return _dispatch(handler, run)


def handle_post(handler: Any, path: str) -> bool:
    if not path.startswith("/api/v2/"):
        return False

    def run() -> None:
        if path.startswith("/api/v2/admin/"):
            return _admin_post(handler, path)
        user = _user(handler)
        if not user:
            return
        uid = _user_id(user)
        if path == "/api/v2/me/avatar":
            _success(handler, _upload_avatar(handler, uid), HTTPStatus.CREATED)
            return
        body = _body(handler)
        if path == "/api/v2/practice-sessions":
            _success(handler, _create_practice(uid, body), HTTPStatus.CREATED)
        elif path == "/api/v2/ai/explain":
            _success(handler, features.explain(_rt(), uid, body))
        elif path == "/api/v2/me/ai-summary":
            _success(handler, features.personal_summary(_rt(), uid, body))
        elif path == "/api/v2/reference-document":
            _success(handler, features.reference_document(_rt(), uid, body))
        elif re.fullmatch(r"/api/v2/practice-sessions/[^/]+/answers", path):
            session_id = path.split("/")[-2]
            _success(handler, _practice_answer(uid, session_id, body), HTTPStatus.CREATED)
        elif path == "/api/v2/question-feedback":
            feedback_id = _rt().service.create_feedback(uid, str(body.get("questionId", "")), str(body.get("type", body.get("feedbackType", "other"))), str(body.get("description", "")))
            _success(handler, {"id": feedback_id}, HTTPStatus.CREATED)
        elif path == "/api/v2/me/password":
            _success(handler, _change_password(handler, uid, body))
        elif path == "/api/v2/me/logout-all":
            if body.get("confirm") is not True:
                raise V2Error("confirmation_required", "需要确认退出所有设备")
            with connect_db(_rt().db_path) as db:
                db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            _success(handler, {"loggedOut": True})
        elif path == "/api/v2/exam-attempts":
            _success(handler, _start_exam(uid, body), HTTPStatus.CREATED)
        elif re.fullmatch(r"/api/v2/exam-attempts/[^/]+/submit", path):
            _success(handler, _submit_exam(uid, path.split("/")[-2], body))
        else:
            _failure(handler, HTTPStatus.NOT_FOUND, "not_found", "接口不存在")

    return _dispatch(handler, run)


def handle_patch(handler: Any, path: str) -> bool:
    if not path.startswith("/api/v2/"):
        return False

    def run() -> None:
        if path.startswith("/api/v2/admin/"):
            return _admin_patch(handler, path)
        user = _user(handler)
        if not user:
            return
        if path == "/api/v2/me/appearance":
            _success(handler, features.preferences(_rt(), 'user', _user_id(user), _body(handler)))
        elif path == "/api/v2/me/profile":
            _success(handler, _update_profile(_user_id(user), _body(handler)))
        elif re.fullmatch(r"/api/v2/practice-sessions/[^/]+", path):
            _success(handler, _update_practice(_user_id(user), path.rsplit("/",1)[1], _body(handler)))
        else:
            _failure(handler, HTTPStatus.NOT_FOUND, "not_found", "接口不存在")

    return _dispatch(handler, run)


def handle_put(handler: Any, path: str) -> bool:
    if not path.startswith("/api/v2/"):
        return False

    def run() -> None:
        user = _user(handler)
        if not user:
            return
        if re.fullmatch(r"/api/v2/exam-attempts/[^/]+/answers", path):
            _success(handler, _save_exam_answer(_user_id(user), path.split("/")[-2], _body(handler)))
        else:
            _failure(handler, HTTPStatus.NOT_FOUND, "not_found", "接口不存在")

    return _dispatch(handler, run)


def _home(user_id: int) -> dict[str, Any]:
    now, today = int(time.time()), time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    with connect_db(_rt().db_path) as db:
        legacy = db.execute("SELECT COUNT(*) answered, COALESCE(SUM(correct),0) correct FROM attempts WHERE user_id=? AND skipped=0", (user_id,)).fetchone()
        v2 = db.execute(
            """SELECT COUNT(*) answered, COALESCE(SUM(a.correct),0) correct FROM practice_answers a
               JOIN practice_sessions s ON s.id=a.session_id WHERE a.user_id=? AND s.mode='practice'""", (user_id,),
        ).fetchone()
        today_count = db.execute(
            """SELECT (SELECT COUNT(*) FROM attempts WHERE user_id=? AND skipped=0 AND date(created_at,'unixepoch','+8 hours')=?) +
               (SELECT COUNT(*) FROM practice_answers a JOIN practice_sessions s ON s.id=a.session_id
                WHERE a.user_id=? AND s.mode='practice' AND date(a.answered_at,'unixepoch','+8 hours')=?)""",
            (user_id, today, user_id, today),
        ).fetchone()[0]
        active = db.execute("SELECT id, topic, updated_at FROM practice_sessions WHERE user_id=? AND status='active' ORDER BY updated_at DESC LIMIT 1", (user_id,)).fetchone()
        last = db.execute("SELECT id, topic, mode, updated_at FROM practice_sessions WHERE user_id=? ORDER BY updated_at DESC LIMIT 1", (user_id,)).fetchone()
        recent_exam = db.execute("SELECT e.score,e.submitted_at,p.title FROM exam_attempts e JOIN exam_papers p ON p.id=e.paper_id WHERE e.user_id=? AND e.status='submitted' ORDER BY e.submitted_at DESC LIMIT 1", (user_id,)).fetchone()
        announcements = db.execute("SELECT id,title,body_text,published_at FROM content_articles WHERE content_type='announcement' AND status='published' ORDER BY published_at DESC LIMIT 5").fetchall()
        weak = db.execute(
            """SELECT q.topic name, COUNT(*) samples, ROUND(100.0*SUM(a.correct)/COUNT(*),1) accuracy
               FROM practice_answers a JOIN practice_sessions s ON s.id=a.session_id JOIN questions q ON q.id=a.question_id
               WHERE a.user_id=? AND s.mode='practice' GROUP BY q.topic HAVING COUNT(*)>=3 ORDER BY accuracy LIMIT 3""", (user_id,),
        ).fetchall()
        question_count = db.execute("SELECT COUNT(*) FROM questions WHERE status='published' AND deleted_at IS NULL").fetchone()[0]
        adjusted=features.adjust_metrics(db,user_id,{"answered":legacy["answered"]+v2["answered"],"correct":legacy["correct"]+v2["correct"]})
    answered, correct = adjusted['answered'], adjusted['correct']
    return {"metrics": {"todayAnswered": today_count, "answered": answered, "correct": correct,
            "accuracy": round(correct * 100 / answered, 1) if answered else 0, "questionCount": question_count},
            "activePractice": {"id": active["id"], "updatedAt": active["updated_at"]} if active else None,
            "lastPractice": {"id": last["id"], "title": ('背题模式' if last['mode']=='memorize' else last['topic'] or '练习'), "updatedAt": last["updated_at"]} if last else None,
            "weakPoints": [dict(row) for row in weak], "recentExam": dict(recent_exam) if recent_exam else None,
            "announcements": [{"id": row["id"], "title": row["title"], "body": row["body_text"], "publishedAt": row["published_at"]} for row in announcements]}


def _questions(user_id: int, query: dict[str, list[str]]) -> dict[str, Any]:
    page, size, offset = _paging(query)
    clauses, params = ["q.status='published'", "q.deleted_at IS NULL"], []
    for field, column in (("topic", "q.topic"), ("type", "q.question_type"), ("difficulty", "q.difficulty"), ("source", "q.source")):
        value = _q(query, field)
        if value:
            clauses.append(f"{column}=?"); params.append(value)
    keyword = _q(query, "q").strip()
    if keyword:
        clauses.append("(r.payload_json LIKE ? OR q.id LIKE ?)"); params.extend([f"%{keyword}%", f"%{keyword}%"])
    state = _q(query, "state")
    if state == "unanswered":
        clauses.append("NOT EXISTS (SELECT 1 FROM question_state s WHERE s.user_id=? AND s.question_id=q.id AND s.last_correct IS NOT NULL)"); params.append(user_id)
    elif state == "wrong":
        clauses.append("EXISTS (SELECT 1 FROM question_state s WHERE s.user_id=? AND s.question_id=q.id AND s.last_correct=0)"); params.append(user_id)
    elif state == "correct":
        clauses.append("EXISTS (SELECT 1 FROM question_state s WHERE s.user_id=? AND s.question_id=q.id AND s.last_correct=1)"); params.append(user_id)
    elif state == "favorite":
        clauses.append("EXISTS (SELECT 1 FROM question_state s WHERE s.user_id=? AND s.question_id=q.id AND s.favorite=1)"); params.append(user_id)
    where = " AND ".join(clauses)
    with connect_db(_rt().db_path) as db:
        total = db.execute(f"SELECT COUNT(*) FROM questions q JOIN question_revisions r ON r.id=q.current_revision_id WHERE {where}", params).fetchone()[0]
        rows = db.execute(f"SELECT q.*,r.payload_json FROM questions q JOIN question_revisions r ON r.id=q.current_revision_id WHERE {where} ORDER BY q.id LIMIT ? OFFSET ?", (*params, size, offset)).fetchall()
        topics = [dict(row) for row in db.execute("SELECT topic name,topic value,COUNT(*) count FROM questions WHERE status='published' AND deleted_at IS NULL GROUP BY topic ORDER BY count DESC")]
    return {"items": [_question_public(json.loads(row["payload_json"])) for row in rows], "page": page, "pageSize": size, "total": total, "facets": {"topics": topics}}


def _create_practice(user_id: int, body: dict[str, Any]) -> dict[str, Any]:
    mode = "memorize" if body.get("mode") in {"study", "memorize"} else "practice"
    ids = body.get("questionIds")
    if body.get("allWrong"):
        mode = "practice"
        with connect_db(_rt().db_path) as db:
            ids = [r[0] for r in db.execute("SELECT q.id FROM questions q JOIN question_state s ON s.question_id=q.id WHERE s.user_id=? AND s.last_correct=0 AND q.status='published' AND q.deleted_at IS NULL ORDER BY RANDOM()", (user_id,))]
        if not ids:raise V2Error("no_wrong_questions", "错题本暂无可练习题目")
    if not ids:
        filters = body.get("filters") or {}
        clauses, params = ["status='published'", "deleted_at IS NULL"], []
        for key, column in (("topic", "topic"), ("type", "question_type"), ("difficulty", "difficulty"), ("source", "source")):
            if filters.get(key): clauses.append(f"{column}=?"); params.append(filters[key])
        keyword=str(filters.get('q','')).strip()
        if keyword:
            clauses.append('(id LIKE ? OR EXISTS (SELECT 1 FROM question_revisions r WHERE r.id=questions.current_revision_id AND r.payload_json LIKE ?))')
            params.extend([f'%{keyword}%']*2)
        state = filters.get("state")
        if state == "unanswered": clauses.append("NOT EXISTS (SELECT 1 FROM question_state s WHERE s.user_id=? AND s.question_id=questions.id AND s.last_correct IS NOT NULL)"); params.append(user_id)
        elif state == "wrong": clauses.append("EXISTS (SELECT 1 FROM question_state s WHERE s.user_id=? AND s.question_id=questions.id AND s.last_correct=0)"); params.append(user_id)
        elif state == "correct": clauses.append("EXISTS (SELECT 1 FROM question_state s WHERE s.user_id=? AND s.question_id=questions.id AND s.last_correct=1)"); params.append(user_id)
        elif state == "favorite": clauses.append("EXISTS (SELECT 1 FROM question_state s WHERE s.user_id=? AND s.question_id=questions.id AND s.favorite=1)"); params.append(user_id)
        requested=body.get('count',filters.get('count',20))
        unlimited=mode=='memorize' and requested=='all'
        if mode=='memorize' and not unlimited and (type(requested) is not int or requested not in (20,60,80)):
            raise V2Error('invalid_count','背题题数请选择20、60、80或不限')
        count = min(100, max(1, int(requested))) if not unlimited else None
        with connect_db(_rt().db_path) as db:
            ids = [row[0] for row in db.execute(f"SELECT id FROM questions WHERE {' AND '.join(clauses)} ORDER BY RANDOM()"+('' if unlimited else ' LIMIT ?'), params if unlimited else (*params,count))]
    if not ids:raise V2Error('no_questions','当前条件下没有可用题目，请调整筛选')
    session = _rt().service.create_practice_session(user_id, list(dict.fromkeys(map(str, ids))), mode=mode, topic=str(body.get("topic", "")))
    with connect_db(_rt().db_path) as db:
        questions = [_question_public(_question_row_payload(db, qid)[1]) for qid in session["questionIds"]]
    return {"session": {**session, "questions": questions, "index": 0, "answers": {}, "title": "背题模式" if mode == "memorize" else "练习模式"}}


def _practice_detail(user_id: int, session_id: str) -> dict[str, Any]:
    with connect_db(_rt().db_path) as db:
        session = db.execute("SELECT * FROM practice_sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
        if not session: raise V2Error("practice_not_found", "练习不存在")
        ids = json.loads(session["question_ids_json"])
        questions = [_question_public(_question_row_payload(db, qid)[1]) for qid in ids]
        answers = {}
        for row in db.execute("SELECT * FROM practice_answers WHERE session_id=?", (session_id,)):
            result = _rt().service._practice_result(row)
            answers[row["question_id"]] = {**result, "selected": json.loads(row["selected_json"]), "revealed": True}
    return {"id": session_id, "mode": "study" if session["mode"] == "memorize" else "practice", "questions": questions,
            "answers": answers, "index": session["current_index"], "title": session["topic"] or "练习"}


def _practice_answer(user_id: int, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    if body.get("studyReveal"):
        with connect_db(_rt().db_path) as db:
            session = db.execute("SELECT * FROM practice_sessions WHERE id=? AND user_id=? AND mode='memorize'", (session_id, user_id)).fetchone()
            if not session or str(body.get("questionId")) not in json.loads(session["question_ids_json"]):
                raise V2Error("practice_not_found", "背题会话不存在")
            _, payload = _question_row_payload(db, str(body.get("questionId")))
        return {"studyReveal": True, "correct": None, "answer": payload.get("answer", []), "explanation": payload.get("explanation", ""), "source": payload.get("source", ""), "references": payload.get("references", []), "explanationConfidence": payload.get("explanationConfidence","medium"), "needsReview": bool(payload.get("needsReview"))}
    return _rt().service.submit_practice_answer(user_id, session_id, str(body.get("questionId", "")), body.get("selected", []), str(body.get("submissionId", "")), int(body.get("durationSeconds", 0)))


def _update_practice(user_id:int,session_id:str,body:dict[str,Any])->dict[str,Any]:
    status=str(body.get("status","active"));index=max(0,int(body.get("currentIndex",0)))
    if status not in {"active","completed","abandoned"}:raise V2Error("invalid_status","练习状态无效")
    now=int(time.time())
    with connect_db(_rt().db_path) as db:
        row=db.execute("SELECT question_ids_json FROM practice_sessions WHERE id=? AND user_id=?",(session_id,user_id)).fetchone()
        if not row:raise V2Error("practice_not_found","练习不存在")
        total=len(json.loads(row["question_ids_json"]));index=min(index,max(0,total-1))
        db.execute("UPDATE practice_sessions SET current_index=?,status=?,updated_at=?,completed_at=CASE WHEN ?='completed' THEN ? ELSE completed_at END WHERE id=?",(index,status,now,status,now,session_id))
    return {"id":session_id,"status":status,"currentIndex":index}


def _learning(user_id: int) -> dict[str, Any]:
    with connect_db(_rt().db_path) as db:
        rows = db.execute("""SELECT a.correct,a.answered_at,q.topic,a.question_id FROM practice_answers a JOIN practice_sessions s ON s.id=a.session_id JOIN questions q ON q.id=a.question_id WHERE a.user_id=? AND s.mode='practice'""", (user_id,)).fetchall()
        states = db.execute("""SELECT s.question_id,s.last_correct,s.favorite,r.payload_json FROM question_state s
          LEFT JOIN questions q ON q.id=s.question_id LEFT JOIN question_revisions r ON r.id=q.current_revision_id
          WHERE s.user_id=?""", (user_id,)).fetchall()
        trend = [dict(row) for row in db.execute("""SELECT date(a.answered_at,'unixepoch','+8 hours') day,COUNT(*) answered,SUM(a.correct) correct FROM practice_answers a JOIN practice_sessions s ON s.id=a.session_id WHERE a.user_id=? AND s.mode='practice' GROUP BY day ORDER BY day DESC LIMIT 30""", (user_id,))]
        totals=features.adjust_metrics(db,user_id,features.actual_metrics(db,user_id))
    answered, correct = totals['answered'], totals['correct']
    def summary(row):
        payload=json.loads(row["payload_json"]) if row["payload_json"] else {}
        return {"id":row["question_id"],"question":payload.get("question","题目已更新"),"topic":payload.get("topic","未分类")}
    return {"metrics": {"answered": answered, "correct": correct, "accuracy": correct * 100 / answered if answered else 0,
            "studyDays": len({time.strftime('%Y-%m-%d', time.gmtime(row['answered_at'] + 28800)) for row in rows}), "streakDays": 0,
            "wrongCount":totals['wrong'],"favoriteCount":totals['favorites'],"examCount":totals['exams'],"submittedExams":totals['submittedExams'],"feedbackCount":totals['feedback']},
            "trend": list(reversed(trend)), "wrongItems": [summary(r) for r in states if r["last_correct"] == 0],
            "favoriteItems": [summary(r) for r in states if r["favorite"] == 1]}


def _heatmap(user_id: int) -> dict[str, Any]:
    with connect_db(_rt().db_path) as db:
        rows = db.execute("""SELECT date(a.answered_at,'unixepoch','+8 hours') day,COUNT(*) count FROM practice_answers a JOIN practice_sessions s ON s.id=a.session_id WHERE a.user_id=? AND s.mode='practice' GROUP BY day ORDER BY day""", (user_id,)).fetchall()
    return {"items": [dict(row) for row in rows]}


def _profile(user_id: int) -> dict[str, Any]:
    with connect_db(_rt().db_path) as db:
        row = db.execute("SELECT id,username,display_name,avatar_url,created_at FROM users WHERE id=?", (user_id,)).fetchone()
    return {"id": row["id"], "username": row["username"], "displayName": row["display_name"], "avatarUrl": row["avatar_url"] or "", "createdAt": row["created_at"]}


def _update_profile(user_id: int, body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("displayName", "")).strip()
    if not 1 <= len(name) <= 30:
        raise V2Error("invalid_profile", "昵称长度需为1至30个字符")
    with connect_db(_rt().db_path) as db:
        db.execute("UPDATE users SET display_name=?,updated_at=? WHERE id=?", (name, int(time.time()), user_id))
    return _profile(user_id)


def _multipart_file(handler: Any, field_name: str, maximum: int) -> bytes:
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except (TypeError, ValueError):
        raise V2Error("invalid_upload", "上传文件格式或大小无效") from None
    content_type = handler.headers.get("Content-Type", "")
    if length <= 0 or length > maximum + 65536 or "multipart/form-data" not in content_type:
        raise V2Error("invalid_upload", "上传文件格式或大小无效")
    match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
    if not match:
        raise V2Error("invalid_upload", "上传边界无效")
    boundary = (match.group(1) or match.group(2)).encode()
    for part in handler.rfile.read(length).split(b"--" + boundary):
        head, separator, data = part.partition(b"\r\n\r\n")
        if separator and f'name="{field_name}"'.encode() in head:
            if data.endswith(b"\r\n"):
                data = data[:-2]
            if not data or len(data) > maximum:
                raise V2Error("invalid_upload", "上传文件为空或超过2MB")
            return data
    raise V2Error("invalid_upload", "未找到上传文件")


def _upload_avatar(handler: Any, user_id: int) -> dict[str, Any]:
    data = _multipart_file(handler, "avatar", _AVATAR_MAX)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = "png"
    elif data.startswith(b"\xff\xd8\xff"):
        extension = "jpg"
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        extension = "webp"
    else:
        raise V2Error("invalid_upload", "头像仅支持PNG、JPEG或WebP图片")
    avatar_dir = _rt().db_path.parent / "uploads" / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    filename = f"user-{user_id}-{hashlib.sha256(data).hexdigest()[:12]}.{extension}"
    destination = avatar_dir / filename
    temporary = avatar_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
    temporary.write_bytes(data)
    os.replace(temporary, destination)
    avatar_url = f"/uploads/avatars/{filename}"
    with connect_db(_rt().db_path) as db:
        db.execute("UPDATE users SET avatar_url=?,updated_at=? WHERE id=?", (avatar_url, int(time.time()), user_id))
    return {"avatarUrl": avatar_url}


def _change_password(handler: Any, user_id: int, body: dict[str, Any]) -> dict[str, Any]:
    current, new = str(body.get("currentPassword", "")), str(body.get("newPassword", ""))
    if len(new) < 8 or len(new) > 128: raise V2Error("invalid_password", "新密码长度需为8至128位")
    with connect_db(_rt().db_path) as db:
        row = db.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
        if not row or not verify_password(current, row["password_hash"]): raise V2Error("invalid_password", "当前密码不正确")
        db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (encode_password(new), int(time.time()), user_id))
        token = handler._session_token() if hasattr(handler, "_session_token") else None
        if token:
            digest = hashlib.sha256(token.encode("ascii")).hexdigest()
            db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user_id, digest))
        else: db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    return {"changed": True}


def _my_feedback(user_id: int, query: dict[str, list[str]]) -> dict[str, Any]:
    page, size, offset = _paging(query)
    with connect_db(_rt().db_path) as db:
        total = db.execute("SELECT COUNT(*) FROM question_feedback WHERE user_id=?", (user_id,)).fetchone()[0]
        rows = db.execute("SELECT * FROM question_feedback WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?", (user_id, size, offset)).fetchall()
    return {"items": [{"id": r["id"], "questionId": r["question_id"], "type": r["feedback_type"], "description": r["description"], "status": r["status"], "createdAt": r["created_at"]} for r in rows], "page": page, "pageSize": size, "total": total}


def _leaderboard(user_id: int, period: str) -> dict[str, Any]:
    return features.leaderboard(_rt(), period)


def _public_profile(user_id: int) -> dict[str, Any]:
    with connect_db(_rt().db_path) as db:
        user = db.execute("SELECT id,username,display_name,avatar_url FROM users WHERE id=? AND status='active'", (user_id,)).fetchone()
        if not user:
            raise V2Error("user_not_found", "用户不存在")
        legacy = db.execute("SELECT COUNT(*) answered,COALESCE(SUM(correct),0) correct FROM attempts WHERE user_id=? AND skipped=0", (user_id,)).fetchone()
        practice = db.execute("""SELECT COUNT(*) answered,COALESCE(SUM(a.correct),0) correct,
          COALESCE(AVG(CASE WHEN a.duration_seconds>0 THEN a.duration_seconds END),0) average_seconds
          FROM practice_answers a JOIN practice_sessions s ON s.id=a.session_id
          WHERE a.user_id=? AND s.mode='practice'""", (user_id,)).fetchone()
        totals=features.adjust_metrics(db,user_id,{"answered":legacy["answered"]+practice["answered"],"correct":legacy["correct"]+practice["correct"]})
    answered,correct=totals['answered'],totals['correct']
    return {"id": user["id"], "username": user["username"], "displayName": user["display_name"],
            "avatarUrl": user["avatar_url"] or "", "practiceAnswered": answered,
            "accuracy": round(correct * 100 / answered, 1) if answered else 0,
            "averageDurationSeconds": round(float(practice["average_seconds"]), 1) if practice["average_seconds"] else 0}


def _exam_papers(user_id:int) -> dict[str, Any]:
    with connect_db(_rt().db_path) as db:
        rows = db.execute("""SELECT p.*,COUNT(i.position) question_count FROM exam_papers p LEFT JOIN exam_paper_items i ON i.paper_id=p.id WHERE p.status='published' GROUP BY p.id ORDER BY p.updated_at DESC""").fetchall()
        active=db.execute("""SELECT e.id,e.started_at,e.deadline_at,p.title FROM exam_attempts e JOIN exam_papers p ON p.id=e.paper_id
          WHERE e.user_id=? AND e.status='active' ORDER BY e.started_at DESC LIMIT 5""",(user_id,)).fetchall()
    return {"items": [{"id": r["id"], "title": r["title"], "questionCount": r["question_count"], "durationMinutes": r["duration_seconds"] // 60, "passScore": r["pass_score"]} for r in rows],
            "activeAttempts":[{"id":r["id"],"title":r["title"],"startedAt":r["started_at"],"endsAt":r["deadline_at"]} for r in active]}


def _start_exam(user_id: int, body: dict[str, Any]) -> dict[str, Any]:
    paper_id = str(body.get("paperId", ""))
    if not paper_id:
        config = body.get("configuration") or {}
        count = max(1, min(200, int(config.get("questionCount", 50))))
        clauses, params = ["status='published'", "deleted_at IS NULL"], []
        if config.get("topic"):
            clauses.append("topic=?"); params.append(str(config["topic"]))
        with connect_db(_rt().db_path) as db:
            question_ids = [row[0] for row in db.execute(
                f"SELECT id FROM questions WHERE {' AND '.join(clauses)} ORDER BY RANDOM() LIMIT ?", (*params, count),
            )]
        if len(question_ids) < count:
            raise V2Error("insufficient_questions", f"当前条件只有 {len(question_ids)} 道题，无法生成 {count} 道不重复试卷")
        paper_id = _rt().service.create_exam_paper(
            _rt().bootstrap_admin_id,
            "自定义随机卷",
            question_ids,
            duration_seconds=max(60, min(300, int(config.get("durationMinutes", 60))) * 60),
            pass_score=max(1, min(100, float(config.get("passScore", 60)))),
            published=True,
        )
    attempt_id = _rt().service.start_exam_attempt(user_id, paper_id)
    return {"attempt": _exam_attempt(user_id, attempt_id)}


def _exam_attempt(user_id: int, attempt_id: str) -> dict[str, Any]:
    with connect_db(_rt().db_path) as db:
        attempt = db.execute("SELECT e.*,p.title FROM exam_attempts e JOIN exam_papers p ON p.id=e.paper_id WHERE e.id=? AND e.user_id=?", (attempt_id, user_id)).fetchone()
        if not attempt: raise V2Error("attempt_not_found", "考试记录不存在")
        items = db.execute("SELECT * FROM exam_attempt_items WHERE attempt_id=? ORDER BY position", (attempt_id,)).fetchall()
        answers = {r["position"]: json.loads(r["selected_json"]) for r in db.execute("SELECT * FROM exam_answers WHERE attempt_id=?", (attempt_id,))}
    questions = []
    by_id = {}
    for row in items:
        payload = _question_public(json.loads(row["snapshot_json"]), include_source=False)
        questions.append(payload); by_id[payload["id"]] = answers.get(row["position"], [])
    return {"id": attempt_id, "title": attempt["title"], "status": attempt["status"], "startedAt": attempt["started_at"], "endsAt": attempt["deadline_at"], "questions": questions, "answers": by_id, "marked": []}


def _save_exam_answer(user_id: int, attempt_id: str, body: dict[str, Any]) -> dict[str, Any]:
    question_id, selected, now = str(body.get("questionId", "")), body.get("selected", []), int(time.time())
    if not isinstance(selected, list): raise V2Error("invalid_answer", "答案格式无效")
    with connect_db(_rt().db_path) as db:
        attempt = db.execute("SELECT * FROM exam_attempts WHERE id=? AND user_id=?", (attempt_id, user_id)).fetchone()
        if not attempt or attempt["status"] != "active" or now > attempt["deadline_at"]: raise V2Error("attempt_closed", "考试已结束")
        item = db.execute("SELECT * FROM exam_attempt_items WHERE attempt_id=? AND question_id=?", (attempt_id, question_id)).fetchone()
        if not item: raise V2Error("question_not_found", "题目不属于该考试")
        payload = json.loads(item["snapshot_json"]); valid = {str(o["key"]) for o in payload.get("options", [])}; selected = sorted(set(map(str, selected)))
        if not set(selected).issubset(valid): raise V2Error("invalid_option", "答案包含无效选项")
        db.execute("""INSERT INTO exam_answers(attempt_id,position,selected_json,correct,answered_at) VALUES (?,?,?,?,?)
          ON CONFLICT(attempt_id,position) DO UPDATE SET selected_json=excluded.selected_json,correct=NULL,answered_at=excluded.answered_at""",
          (attempt_id, item["position"], json.dumps(selected), None, now))
    return {"saved": True, "questionId": question_id}


def _submit_exam(user_id: int, attempt_id: str, body: dict[str, Any]) -> dict[str, Any]:
    if body.get("confirm") is not True: raise V2Error("confirmation_required", "交卷前需要确认")
    now = int(time.time())
    with connect_db(_rt().db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            attempt = db.execute("SELECT * FROM exam_attempts WHERE id=? AND user_id=?", (attempt_id, user_id)).fetchone()
            if not attempt: raise V2Error("attempt_not_found", "考试记录不存在")
            if attempt["status"] == "submitted": db.rollback(); return {"submitted": True, "score": attempt["score"]}
            items = db.execute("SELECT * FROM exam_attempt_items WHERE attempt_id=?", (attempt_id,)).fetchall()
            total = earned = 0.0
            for item in items:
                total += item["points"]; payload = json.loads(item["snapshot_json"])
                answer = db.execute("SELECT * FROM exam_answers WHERE attempt_id=? AND position=?", (attempt_id, item["position"])).fetchone()
                correct = bool(answer and sorted(json.loads(answer["selected_json"])) == sorted(payload.get("answer", [])))
                if answer: db.execute("UPDATE exam_answers SET correct=? WHERE attempt_id=? AND position=?", (int(correct), attempt_id, item["position"]))
                if correct: earned += item["points"]
            score = round(earned * 100 / total, 2) if total else 0
            db.execute("UPDATE exam_attempts SET status='submitted',submitted_at=?,score=? WHERE id=?", (now, score, attempt_id)); db.commit()
        except Exception: db.rollback(); raise
    return {"submitted": True, "score": score}


def _exam_report(user_id: int, attempt_id: str) -> dict[str, Any]:
    with connect_db(_rt().db_path) as db:
        attempt = db.execute("SELECT e.*,p.title,p.pass_score FROM exam_attempts e JOIN exam_papers p ON p.id=e.paper_id WHERE e.id=? AND e.user_id=? AND e.status='submitted'", (attempt_id, user_id)).fetchone()
        if not attempt: raise V2Error("attempt_not_found", "成绩报告不存在")
        items = db.execute("SELECT i.*,a.selected_json,a.correct FROM exam_attempt_items i LEFT JOIN exam_answers a ON a.attempt_id=i.attempt_id AND a.position=i.position WHERE i.attempt_id=? ORDER BY i.position", (attempt_id,)).fetchall()
    details=[]
    for row in items:
        p=json.loads(row["snapshot_json"]); details.append({"question":_question_public(p),"selected":json.loads(row["selected_json"]) if row["selected_json"] else [],"correct":bool(row["correct"]),"answer":p.get("answer",[]),"explanation":p.get("explanation",""),"references":p.get("references",[]),"explanationConfidence":p.get("explanationConfidence","medium"),"needsReview":bool(p.get("needsReview"))})
    correct_count=sum(1 for item in details if item["correct"])
    return {"id":attempt_id,"title":attempt["title"],"score":attempt["score"],"accuracy":round(100*correct_count/len(details),1) if details else 0,"correctCount":correct_count,"questionCount":len(details),"passed":attempt["score"]>=attempt["pass_score"],"durationSeconds":attempt["submitted_at"]-attempt["started_at"],"items":details}


def _content_public(content_type: str, query: dict[str, list[str]]) -> dict[str, Any]:
    if content_type not in {"article","announcement","changelog"}: raise V2Error("invalid_content_type", "内容类型无效")
    page,size,offset=_paging(query)
    with connect_db(_rt().db_path) as db:
        total=db.execute("SELECT COUNT(*) FROM content_articles WHERE content_type=? AND status='published'",(content_type,)).fetchone()[0]
        rows=db.execute("SELECT * FROM content_articles WHERE content_type=? AND status='published' ORDER BY published_at DESC LIMIT ? OFFSET ?",(content_type,size,offset)).fetchall()
    return {"items":[{"id":r["id"],"type":r["content_type"],"title":r["title"],"body":r["body_text"],"source":r["source"],"publishedAt":r["published_at"]} for r in rows],"page":page,"pageSize":size,"total":total}


def _admin_get(handler: Any, path: str, query: dict[str, list[str]]) -> None:
    if path == "/api/v2/admin/me/appearance":
        if not handler._require_admin():return
        aid=int(getattr(handler,"v2_admin_id",_rt().bootstrap_admin_id))
        return _success(handler,features.preferences(_rt(),'admin',aid))
    permission = "dashboard.read"
    if "/admins" in path: permission="roles.manage"
    elif "/registration-codes" in path: permission="registration_codes.manage"
    elif "/audit-logs" in path: permission="audit.read"
    elif path=="/api/v2/admin/backups": permission="backups.read"
    elif "/questions" in path: permission="questions.read"
    elif "/users" in path: permission="users.read"
    elif "/exam-papers" in path: permission="papers.read"
    elif "/feedback" in path: permission="feedback.read"
    elif "/content" in path: permission="content.read"
    admin_id=_admin_id(handler,permission)
    if admin_id is None:return
    if path=="/api/v2/admin/dashboard": _success(handler,_admin_dashboard(include_system=_rt().service.has_permission(admin_id,"backups.read")))
    elif path=="/api/v2/admin/admins": _success(handler,_admin_list())
    elif path=="/api/v2/admin/questions": _success(handler,_admin_questions(query))
    elif re.fullmatch(r"/api/v2/admin/questions/[^/]+/revisions",path): _success(handler,_admin_question_revisions(unquote(path.split("/")[-2])))
    elif path=="/api/v2/admin/users": _success(handler,_admin_users(query))
    elif re.fullmatch(r"/api/v2/admin/users/\d+",path): _success(handler,features.user_detail(_rt(),int(path.rsplit('/',1)[1]),query))
    elif path=="/api/v2/admin/registration-codes": _success(handler,_admin_codes(query))
    elif path=="/api/v2/admin/exam-papers": _success(handler,_admin_papers(query))
    elif path=="/api/v2/admin/feedback": _success(handler,_admin_feedback(query))
    elif path=="/api/v2/admin/content": _success(handler,_admin_content(query))
    elif path=="/api/v2/admin/audit-logs": _success(handler,_admin_audit(query))
    elif path=="/api/v2/admin/backups": _success(handler,_admin_backups())
    else:_failure(handler,HTTPStatus.NOT_FOUND,"not_found","接口不存在")


def _admin_post(handler: Any, path: str) -> None:
    if path=="/api/v2/admin/question-imports/preview":
        admin_id=_admin_id(handler,"questions.import");
        if admin_id is not None:_success(handler,_preview_import(handler,admin_id),HTTPStatus.CREATED)
        return
    body=_body(handler)
    if path=="/api/v2/admin/admins":
        admin_id=_admin_id(handler,"roles.manage")
        if admin_id is not None:_success(handler,_admin_create(admin_id,body),HTTPStatus.CREATED)
    elif re.fullmatch(r"/api/v2/admin/question-imports/[^/]+/commit",path):
        admin_id=_admin_id(handler,"questions.import");
        if admin_id is not None:_success(handler,_commit_import(admin_id,path.split("/")[-2],body))
    elif path=="/api/v2/admin/users":
        admin_id=_admin_id(handler,"users.status");
        if admin_id is not None:_success(handler,features.hard_delete_user(_rt(),admin_id,body) if body.get('action')=='hard-delete' else _admin_user_action(admin_id,body))
    elif re.fullmatch(r"/api/v2/admin/users/\d+/ai-portrait",path):
        admin_id=_admin_id(handler,"users.ai")
        if admin_id is not None:_success(handler,features.portrait(_rt(),admin_id,int(path.split('/')[-2]),body))
    elif re.fullmatch(r"/api/v2/admin/users/\d+/password",path):
        admin_id=_admin_id(handler,"users.password")
        if admin_id is not None:_success(handler,features.change_user_password(_rt(),admin_id,int(path.split('/')[-2]),body))
    elif path=="/api/v2/admin/registration-codes":
        admin_id=_admin_id(handler,"registration_codes.manage");
        if admin_id is not None:_success(handler,features.delete_code(_rt(),admin_id,body) if body.get('action')=='delete' else _admin_code_action(admin_id,body),HTTPStatus.OK if body.get('action')=='delete' else HTTPStatus.CREATED)
    elif path=="/api/v2/admin/questions":
        admin_id=_admin_id(handler,"questions.write");
        if admin_id is not None:_success(handler,_admin_create_question(admin_id,body),HTTPStatus.CREATED)
    elif path=="/api/v2/admin/exam-papers":
        admin_id=_admin_id(handler,"papers.write");
        if admin_id is not None:_success(handler,features.delete_paper(_rt(),admin_id,body) if body.get('action')=='delete' else _admin_paper_action(admin_id,body),HTTPStatus.OK if body.get('action')=='delete' else HTTPStatus.CREATED)
    elif path=="/api/v2/admin/content":
        admin_id=_admin_id(handler,"content.write");
        if admin_id is not None:
            content_type,title,body_text,status=str(body.get("type","announcement")),str(body.get("title","")).strip(),str(body.get("body","")).strip(),str(body.get("status","draft"))
            if content_type not in {"article","announcement","changelog"} or not title or not body_text:raise V2Error("invalid_content","内容类型、标题或正文无效")
            if status not in {"draft","published","archived"}:raise V2Error("invalid_status","内容状态无效")
            db_status="unpublished" if status=="archived" else status
            content_id=_rt().service.create_content(admin_id,content_type,title,body_text,str(body.get("source","")).strip(),status=="published",db_status)
            _success(handler,{"id":content_id,"status":status},HTTPStatus.CREATED)
    elif path=="/api/v2/admin/backups":
        admin_id=_admin_id(handler,"backups.create")
        if admin_id is not None:
            if body.get("confirm") is not True:raise V2Error("confirmation_required","创建备份需要确认")
            _success(handler,backup_database(_rt().db_path,_rt().backup_dir),HTTPStatus.CREATED)
    else:_failure(handler,HTTPStatus.NOT_FOUND,"not_found","接口不存在")


def _admin_patch(handler: Any, path: str) -> None:
    body=_body(handler)
    if path == "/api/v2/admin/me/appearance":
        if not handler._require_admin():return
        aid=int(getattr(handler,"v2_admin_id",_rt().bootstrap_admin_id))
        _success(handler,features.preferences(_rt(),'admin',aid,body))
    elif re.fullmatch(r"/api/v2/admin/users/\d+",path):
        aid=_admin_id(handler,"users.edit")
        if aid is not None:_success(handler,features.update_user(_rt(),aid,int(path.rsplit('/',1)[1]),body))
    elif re.fullmatch(r"/api/v2/admin/admins/\d+",path):
        aid=_admin_id(handler,"roles.manage")
        if aid is not None:_success(handler,_admin_update(aid,int(path.rsplit("/",1)[1]),body))
    elif re.fullmatch(r"/api/v2/admin/questions/[^/]+",path):
        aid=_admin_id(handler,"questions.write");
        if aid is not None:_success(handler,_admin_patch_question(aid,unquote(path.rsplit("/",1)[1]),body))
    elif path=="/api/v2/admin/feedback":
        aid=_admin_id(handler,"feedback.write");
        if aid is not None:_success(handler,_admin_patch_feedback(aid,body))
    elif path=="/api/v2/admin/content":
        aid=_admin_id(handler,"content.write");
        if aid is not None:_success(handler,_admin_patch_content(aid,body))
    else:_failure(handler,HTTPStatus.NOT_FOUND,"not_found","接口不存在")


def _admin_dashboard(include_system: bool = False) -> dict[str, Any]:
    today=time.strftime("%Y-%m-%d",time.gmtime(time.time()+28800));cutoff=int(time.time())-13*86400
    with connect_db(_rt().db_path) as db:
        users=db.execute("SELECT COUNT(*) FROM users WHERE status<>'deleted'").fetchone()[0]; new=db.execute("SELECT COUNT(*) FROM users WHERE status<>'deleted' AND date(created_at,'unixepoch','+8 hours')=?",(today,)).fetchone()[0]
        answers=db.execute("SELECT (SELECT COUNT(*) FROM practice_answers)+(SELECT COUNT(*) FROM attempts)").fetchone()[0]; today_answers=db.execute("SELECT (SELECT COUNT(*) FROM practice_answers WHERE date(answered_at,'unixepoch','+8 hours')=?)+(SELECT COUNT(*) FROM attempts WHERE date(created_at,'unixepoch','+8 hours')=?)",(today,today)).fetchone()[0]; feedback=db.execute("SELECT COUNT(*) FROM question_feedback WHERE status IN ('open','processing')").fetchone()[0]
        questions=db.execute("SELECT COUNT(*) FROM questions WHERE deleted_at IS NULL").fetchone()[0]; exams=db.execute("SELECT COUNT(*) FROM exam_attempts").fetchone()[0]
        dau=db.execute("""SELECT COUNT(DISTINCT user_id) FROM (SELECT user_id FROM practice_answers WHERE date(answered_at,'unixepoch','+8 hours')=? UNION SELECT user_id FROM attempts WHERE date(created_at,'unixepoch','+8 hours')=? UNION SELECT user_id FROM exam_attempts WHERE date(started_at,'unixepoch','+8 hours')=?)""",(today,today,today)).fetchone()[0]
        daily=[dict(r) for r in db.execute("""WITH activity AS (SELECT user_id,correct,answered_at occurred_at FROM practice_answers UNION ALL SELECT user_id,correct,created_at FROM attempts) SELECT date(occurred_at,'unixepoch','+8 hours') day,COUNT(*) answers,SUM(correct) correct,COUNT(DISTINCT user_id) activeUsers FROM activity WHERE occurred_at>=? GROUP BY day ORDER BY day""",(cutoff,))]
        topics=[dict(r) for r in db.execute("""WITH activity AS (SELECT q.topic,a.correct FROM practice_answers a JOIN questions q ON q.id=a.question_id UNION ALL SELECT topic,correct FROM attempts) SELECT topic,COUNT(*) answered,SUM(correct) correct,ROUND(100.0*SUM(correct)/COUNT(*),1) accuracy FROM activity GROUP BY topic ORDER BY answered DESC""")]
        risk_rows=db.execute("""WITH activity AS (SELECT question_id,correct FROM practice_answers UNION ALL SELECT question_id,correct FROM attempts) SELECT question_id,COUNT(*) sample_size,SUM(CASE WHEN correct=0 THEN 1 ELSE 0 END) wrong_count,ROUND(100.0*SUM(CASE WHEN correct=0 THEN 1 ELSE 0 END)/COUNT(*),1) wrong_rate FROM activity GROUP BY question_id HAVING COUNT(*)>=3 AND SUM(CASE WHEN correct=0 THEN 1 ELSE 0 END)*1.0/COUNT(*)>=0.5 ORDER BY wrong_rate DESC,sample_size DESC LIMIT 20""").fetchall()
        backups=[dict(r) for r in db.execute("SELECT file_name name,created_at,CASE WHEN restore_verified=1 THEN 'verified' ELSE 'invalid' END status FROM backup_metadata ORDER BY id DESC LIMIT 10")] if include_system else []
    totals={"users":users,"newUsersToday":new,"dau":dau,"answers":answers,"answersToday":today_answers,"questions":questions,"examAttempts":exams,"openFeedback":feedback}
    risk_signals=[{"questionId":r["question_id"],"reason":f"错误率 {r['wrong_rate']:g}%","sampleSize":r["sample_size"],"wrongCount":r["wrong_count"],"status":"open"} for r in risk_rows]
    return {"totals":totals,"metrics":{"users":users,"todayNew":new,"dau":dau,"answers":answers,"todayAnswers":today_answers,"questions":questions,"examAttempts":exams,"feedback":feedback},"daily":daily,"topics":topics,"riskSignals":risk_signals,"backups":backups,"services":[{"name":"NetForge API","status":"active","detail":"database reachable"}],"updatedAt":int(time.time())}


def _admin_backups() -> dict[str, Any]:
    with connect_db(_rt().db_path) as db:
        rows=db.execute("SELECT file_name name,created_at,CASE WHEN restore_verified=1 THEN 'verified' ELSE 'invalid' END status FROM backup_metadata ORDER BY id DESC LIMIT 100").fetchall()
    return {"items":[dict(row) for row in rows]}


def _admin_list():
    with connect_db(_rt().db_path) as db:
        rows=db.execute("""SELECT a.id,a.username,a.display_name,a.status,a.last_login_at,GROUP_CONCAT(ar.role_name) roles
          FROM admin_accounts a LEFT JOIN admin_account_roles ar ON ar.admin_id=a.id GROUP BY a.id ORDER BY a.created_at""").fetchall()
    return {"items":[{"id":r["id"],"username":r["username"],"displayName":r["display_name"],"status":r["status"],"lastLoginAt":r["last_login_at"],"roles":[x for x in (r["roles"] or "").split(",") if x]} for r in rows]}


def _admin_create(actor_id,body):
    password=str(body.get("password",""))
    if len(password)<12 or len(password)>128:raise V2Error("invalid_password","管理员初始密码需为12至128位")
    roles=body.get("roles") or []
    admin_id=_rt().service.create_admin_account(str(body.get("username","")).strip(),password,roles,str(body.get("displayName","")).strip(),actor_id)
    return {"id":admin_id}


def _admin_update(actor_id,target_id,body):
    if body.get("confirm") is not True:raise V2Error("confirmation_required","管理员权限变更需要确认")
    if actor_id==target_id and body.get("status")=="disabled":raise V2Error("self_lockout","不能禁用当前管理员账号")
    roles=body.get("roles")
    status=body.get("status")
    if roles is not None and (not isinstance(roles,list) or not roles or not set(roles).issubset(ROLE_PERMISSIONS)):
        raise V2Error("invalid_role","管理员角色无效")
    if status is not None and status not in {"active","disabled"}:raise V2Error("invalid_status","管理员状态无效")
    with connect_db(_rt().db_path) as db:
        before=db.execute("SELECT id,username,status FROM admin_accounts WHERE id=?",(target_id,)).fetchone()
        if not before:raise V2Error("admin_not_found","管理员不存在")
        current_roles={r[0] for r in db.execute("SELECT role_name FROM admin_account_roles WHERE admin_id=?",(target_id,))}
        removing_super="super_admin" in current_roles and ((roles is not None and "super_admin" not in roles) or status=="disabled")
        if removing_super:
            remaining=db.execute("""SELECT COUNT(DISTINCT a.id) FROM admin_accounts a JOIN admin_account_roles r ON r.admin_id=a.id
              WHERE a.status='active' AND r.role_name='super_admin' AND a.id<>?""",(target_id,)).fetchone()[0]
            if remaining<1:raise V2Error("last_super_admin","不能移除最后一位可用超级管理员")
        if roles is not None:
            db.execute("DELETE FROM admin_account_roles WHERE admin_id=?",(target_id,))
            db.executemany("INSERT INTO admin_account_roles(admin_id,role_name) VALUES(?,?)",[(target_id,r) for r in sorted(set(roles))])
        if status is not None:
            db.execute("UPDATE admin_accounts SET status=? WHERE id=?",(status,target_id))
            if status=="disabled":db.execute("DELETE FROM v2_admin_sessions WHERE admin_id=?",(target_id,))
        _rt().service._audit(db,actor_id,"admin.update","admin",str(target_id),True,{"status":before["status"],"roles":sorted(current_roles)},{"status":status or before["status"],"roles":roles or sorted(current_roles)})
    return {"id":target_id,"updated":True}


def _admin_questions(query: dict[str,list[str]]) -> dict[str,Any]:
    page,size,offset=_paging(query); clauses=["q.deleted_at IS NULL"];params=[]
    if _q(query,"status"):clauses.append("q.status=?");params.append("unpublished" if _q(query,"status")=="archived" else _q(query,"status"))
    if _q(query,"q"):clauses.append("(q.id LIKE ? OR r.payload_json LIKE ?)");params += [f"%{_q(query,'q')}%"]*2
    where=" AND ".join(clauses)
    with connect_db(_rt().db_path) as db:
        total=db.execute(f"SELECT COUNT(*) FROM questions q JOIN question_revisions r ON r.id=q.current_revision_id WHERE {where}",params).fetchone()[0]
        rows=db.execute(f"SELECT q.*,r.payload_json,r.version FROM questions q JOIN question_revisions r ON r.id=q.current_revision_id WHERE {where} ORDER BY q.updated_at DESC LIMIT ? OFFSET ?",(*params,size,offset)).fetchall()
    items=[]
    for r in rows:
        p=json.loads(r["payload_json"]);items.append({**p,"status":"archived" if r["status"]=="unpublished" else r["status"],"difficulty":r["difficulty"],"revision":r["version"],"updatedAt":r["updated_at"]})
    return {"items":items,"page":page,"pageSize":size,"total":total}


def _admin_question_revisions(question_id: str) -> dict[str,Any]:
    with connect_db(_rt().db_path) as db:
        if not db.execute("SELECT 1 FROM questions WHERE id=? AND deleted_at IS NULL",(question_id,)).fetchone():raise V2Error("question_not_found","题目不存在")
        rows=db.execute("""SELECT r.id,r.version,r.change_summary,r.created_at,a.username created_by
          FROM question_revisions r LEFT JOIN admin_accounts a ON a.id=r.created_by
          WHERE r.question_id=? ORDER BY r.version DESC""",(question_id,)).fetchall()
    return {"questionId":question_id,"items":[{"id":r["id"],"version":r["version"],"changeSummary":r["change_summary"],"createdAt":r["created_at"],"createdBy":r["created_by"] or "system"} for r in rows]}


def _admin_users(query: dict[str,list[str]]) -> dict[str,Any]:
    page,size,offset=_paging(query); clauses=["1=1"];params=[]
    if _q(query,"status"):clauses.append("u.status=?");params.append(_q(query,"status"))
    if _q(query,"q"):clauses.append("(u.username LIKE ? OR u.display_name LIKE ?)");params += [f"%{_q(query,'q')}%"]*2
    where=" AND ".join(clauses)
    with connect_db(_rt().db_path) as db:
        total=db.execute(f"SELECT COUNT(*) FROM users u WHERE {where}",params).fetchone()[0]
        rows=db.execute(f"SELECT u.* FROM users u WHERE {where} ORDER BY u.created_at DESC LIMIT ? OFFSET ?",(*params,size,offset)).fetchall()
        items=[]
        for r in rows:
            metrics=features.adjust_metrics(db,r['id'],features.actual_metrics(db,r['id']))
            items.append({"id":r["id"],"username":r["username"],"displayName":r["display_name"],"status":r["status"],"createdAt":r["created_at"],"lastLoginAt":r["last_login_at"],"practiceAnswers":metrics['answered'],"practiceCorrect":metrics['correct'],"examAttempts":metrics['exams']})
    return {"items":items,"page":page,"pageSize":size,"total":total}


def _admin_user_action(admin_id:int,body:dict[str,Any])->dict[str,Any]:
    if body.get("confirm") is not True:raise V2Error("confirmation_required","用户状态操作需要确认")
    action,uid=str(body.get("action")),int(body.get("userId"));reason=str(body.get("reason","")).strip(); now=int(time.time())
    with connect_db(_rt().db_path) as db:
        before=db.execute("SELECT id,username,status,deleted_at FROM users WHERE id=?",(uid,)).fetchone()
        if not before:raise V2Error("user_not_found","用户不存在")
        if action in {"disable","soft-delete"} and not reason:raise V2Error("reason_required","必须填写操作原因")
        result={"userId":uid,"action":action}
        if action=="disable":db.execute("UPDATE users SET status='disabled',delete_reason=?,updated_at=? WHERE id=?",(reason,now,uid));db.execute("DELETE FROM sessions WHERE user_id=?",(uid,))
        elif action=="soft-delete":db.execute("UPDATE users SET status='deleted',deleted_at=?,delete_reason=?,updated_at=? WHERE id=?",(now,reason,now,uid));db.execute("DELETE FROM sessions WHERE user_id=?",(uid,))
        elif action=="restore":db.execute("UPDATE users SET status='active',deleted_at=NULL,delete_reason=NULL,updated_at=? WHERE id=?",(now,uid))
        elif action=="reset-token":
            token=secrets.token_urlsafe(24);expires=now+1800;db.execute("DELETE FROM password_reset_tokens WHERE user_id=? AND used_at IS NULL",(uid,));db.execute("INSERT INTO password_reset_tokens(user_id,token_hash,expires_at,created_by,created_at) VALUES(?,?,?,?,?)",(uid,hashlib.sha256(token.encode()).hexdigest(),expires,admin_id,now));result.update({"token":token,"expiresAt":expires})
        else:raise V2Error("invalid_action","用户操作无效")
        after=db.execute("SELECT id,username,status,deleted_at FROM users WHERE id=?",(uid,)).fetchone();_rt().service._audit(db,admin_id,f"user.{action}","user",str(uid),True,dict(before),dict(after))
    return result


def _admin_codes(query):
    page,size,offset=_paging(query)
    with connect_db(_rt().db_path) as db:
        total=db.execute("SELECT COUNT(*) FROM registration_codes").fetchone()[0];rows=db.execute("SELECT * FROM registration_codes ORDER BY id DESC LIMIT ? OFFSET ?",(size,offset)).fetchall()
    now=int(time.time());items=[]
    for r in rows:
        status="revoked" if r["revoked_at"] else "expired" if r["expires_at"] and r["expires_at"]<=now else "exhausted" if r["max_uses"] is not None and r["used_count"]>=r["max_uses"] else "active"
        items.append({"id":r["id"],"maskedCode":r["code_prefix"]+"••••••","label":r["label"],"usedCount":r["used_count"],"maxUses":r["max_uses"],"expiresAt":r["expires_at"],"status":status})
    return {"items":items,"page":page,"pageSize":size,"total":total}


def _admin_code_action(admin_id,body):
    if body.get("action")=="revoke":
        if body.get("confirm") is not True:raise V2Error("confirmation_required","撤销注册码需要确认")
        cid=int(body.get("codeId"));now=int(time.time())
        with connect_db(_rt().db_path) as db:
            row=db.execute("SELECT revoked_at FROM registration_codes WHERE id=?",(cid,)).fetchone()
            if not row:raise V2Error("registration_code_not_found","注册码不存在")
            if row["revoked_at"]:raise V2Error("registration_code_revoked","注册码已撤销")
            db.execute("UPDATE registration_codes SET revoked_at=? WHERE id=?",(now,cid));_rt().service._audit(db,admin_id,"registration_code.revoke","registration_code",str(cid),True,None,{"revokedAt":now})
        return {"id":cid,"revoked":True}
    return _rt().service.create_registration_code(admin_id,body.get("code"),str(body.get("label","")),body.get("expiresAt"),body.get("maxUses"))


def _normalize_question(body,question_id=None):
    qid=question_id or str(body.get("id") or f"net-custom-{uuid.uuid4().hex[:10]}");options=body.get("options",[]);answer=body.get("answer",[])
    if body.get("type") not in {"single","multiple","judge"} or not str(body.get("question","")).strip() or not isinstance(options,list) or not isinstance(answer,list):raise V2Error("invalid_question","题目字段无效")
    normalized_options=[]
    for option in options:
        if not isinstance(option,dict):raise V2Error("invalid_question","选项格式无效")
        key=str(option.get("key","")).strip().upper();text=str(option.get("text","")).strip()
        if not key or not text:raise V2Error("invalid_question","选项键和内容不能为空")
        normalized_options.append({"key":key,"text":text})
    keys=[option["key"] for option in normalized_options]
    normalized_answer=list(dict.fromkeys(str(value).strip().upper() for value in answer if str(value).strip()))
    if not 2<=len(normalized_options)<=8 or len(set(keys))!=len(keys):raise V2Error("invalid_question","题目需包含2至8个不重复选项")
    if not normalized_answer or not set(normalized_answer).issubset(keys) or not str(body.get("explanation","")).strip():raise V2Error("invalid_answer","答案或解析无效")
    if body["type"] in {"single","judge"} and len(normalized_answer)!=1:raise V2Error("invalid_answer","单选题和判断题只能有一个答案")
    if body["type"]=="multiple" and len(normalized_answer)<2:raise V2Error("invalid_answer","多选题至少需要两个答案")
    difficulty=body.get("difficulty")
    if difficulty not in {None,"easy","medium","hard"}:raise V2Error("invalid_question","题目难度无效")
    confidence=str(body.get("explanationConfidence","medium"))
    if confidence not in {"high","medium","low"}:raise V2Error("invalid_question","解析置信度无效")
    references=[]
    for value in body.get("references",[]) if isinstance(body.get("references",[]),list) else []:
        url=str(value).strip()
        parsed=urlparse(url)
        if url and (parsed.scheme!="https" or not parsed.netloc):raise V2Error("invalid_reference","校订参考必须使用有效的 HTTPS 链接")
        if url and url not in references:references.append(url)
    return {"id":qid,"topic":str(body.get("topic","未分类")).strip() or "未分类","type":body["type"],"difficulty":difficulty,"question":str(body["question"]).strip(),"options":normalized_options,"answer":normalized_answer,"explanation":str(body["explanation"]).strip(),"source":str(body.get("source","")).strip(),"references":references[:5],"explanationConfidence":confidence,"explanationReviewedAt":body.get("explanationReviewedAt"),"needsReview":bool(body.get("needsReview",False))}


def _insert_question(db,admin_id,payload,status="published"):
    payload=dict(payload);status=payload.pop("__import_status",status)
    if status=="offline":status="unpublished"
    now=int(time.time());content=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"));digest=hashlib.sha256(content.encode()).hexdigest();qid=payload["id"]
    db.execute("INSERT INTO questions(id,topic,question_type,difficulty,source,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(qid,payload["topic"],payload["type"],payload.get("difficulty"),payload.get("source",""),"unpublished" if status=="archived" else status,now,now))
    cur=db.execute("INSERT INTO question_revisions(question_id,version,payload_json,content_hash,change_summary,created_by,created_at) VALUES(?,1,?,?,?,?,?)",(qid,content,digest,"created",admin_id,now));db.execute("UPDATE questions SET current_revision_id=? WHERE id=?",(cur.lastrowid,qid));return qid


def _admin_create_question(admin_id,body):
    payload=_normalize_question(body)
    status=str(body.get("status","published"))
    if status not in {"draft","published","archived"}:raise V2Error("invalid_status","题目状态无效")
    with connect_db(_rt().db_path) as db:_insert_question(db,admin_id,payload,status);_rt().service._audit(db,admin_id,"question.create","question",payload["id"],True,None,{"id":payload["id"]})
    return {"id":payload["id"]}


def _admin_patch_question(admin_id,qid,body):
    with connect_db(_rt().db_path) as db:row,payload=_question_row_payload(db,qid)
    if set(body)<= {"status","confirm"}:
        if body.get("confirm") is not True:raise V2Error("confirmation_required","题目状态修改需要确认")
        status="unpublished" if body.get("status")=="archived" else str(body.get("status"));
        if status not in {"draft","published","unpublished"}:raise V2Error("invalid_status","状态无效")
        with connect_db(_rt().db_path) as db:db.execute("UPDATE questions SET status=?,updated_at=? WHERE id=?",(status,int(time.time()),qid));_rt().service._audit(db,admin_id,"question.status","question",qid,True,{"status":row["status"]},{"status":status})
        return {"id":qid,"status":"archived" if status=="unpublished" else status}
    desired_status=str(body.get("status","archived" if row["status"]=="unpublished" else row["status"]));db_status="unpublished" if desired_status=="archived" else desired_status
    if db_status not in {"draft","published","unpublished"}:raise V2Error("invalid_status","题目状态无效")
    merged={**payload,**body,"id":qid};normalized=_normalize_question(merged,qid);rev=_rt().service.revise_question(admin_id,qid,normalized,str(body.get("changeSummary","edited")),db_status)
    return {"id":qid,"revisionId":rev}


def _parse_multipart_xlsx(handler)->bytes:
    length=int(handler.headers.get("Content-Length","0"));ctype=handler.headers.get("Content-Type","")
    if length<=0 or length>_IMPORT_MAX or "multipart/form-data" not in ctype:raise V2Error("invalid_upload","仅支持不超过10MB的xlsx文件")
    match=re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))",ctype)
    if not match:raise V2Error("invalid_upload","上传边界无效")
    boundary=(match.group(1) or match.group(2)).encode();raw=handler.rfile.read(length)
    for part in raw.split(b"--"+boundary):
        head,sep,data=part.partition(b"\r\n\r\n")
        if sep and b'name="file"' in head:
            return data.rstrip(b"\r\n-")
    raise V2Error("invalid_upload","未找到上传文件")


def _xlsx_rows(data:bytes)->list[dict[str,str]]:
    try:
        archive=zipfile.ZipFile(io.BytesIO(data));shared=[]
        if "xl/sharedStrings.xml" in archive.namelist():
            root=ET.fromstring(archive.read("xl/sharedStrings.xml"));shared=["".join(node.itertext()) for node in root]
        sheet=ET.fromstring(archive.read("xl/worksheets/sheet1.xml"));ns={"x":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"};matrix=[]
        for row in sheet.findall(".//x:sheetData/x:row",ns):
            values={}
            for cell in row.findall("x:c",ns):
                ref=cell.attrib.get("r","");col=re.match(r"[A-Z]+",ref).group(0);kind=cell.attrib.get("t");v=cell.find("x:v",ns);inline=cell.find("x:is",ns)
                text="" if v is None and inline is None else "".join(inline.itertext()) if inline is not None else v.text or ""
                if kind=="s" and text: text=shared[int(text)]
                values[col]=text
            matrix.append(values)
        if not matrix:return []
        cols=sorted(matrix[0],key=lambda x:(len(x),x));headers=[matrix[0].get(c,"").strip() for c in cols]
        return [{headers[i]:row.get(col,"").strip() for i,col in enumerate(cols) if headers[i]} for row in matrix[1:] if any(row.values())]
    except (KeyError,zipfile.BadZipFile,ET.ParseError,ValueError,AttributeError) as exc:raise V2Error("invalid_xlsx","Excel文件结构无效") from exc


def _row_question(row):
    options=[];raw=row.get("options","")
    if raw:
        try:options=json.loads(raw)
        except json.JSONDecodeError:
            for part in raw.split("|"):
                if ":" in part:
                    k,t=part.split(":",1);options.append({"key":k.strip(),"text":t.strip()})
    else:
        for key in "abcdefgh":
            text=str(row.get(f"option_{key}","")).strip()
            if text:options.append({"key":key.upper(),"text":text})
    answer=[x.strip() for x in re.split(r"[,，、|]",row.get("answer","")) if x.strip()]
    question=_normalize_question({"id":row.get("external_id") or row.get("id") or None,"topic":row.get("topic","未分类"),"type":row.get("type","single"),"difficulty":row.get("difficulty") or None,"question":row.get("question",""),"options":options,"answer":answer,"explanation":row.get("explanation",""),"source":row.get("source","")})
    status=str(row.get("status","draft") or "draft").strip()
    if status not in {"draft","published","offline"}:raise V2Error("invalid_status","题目状态无效")
    question["__import_status"]=status
    return question


def _preview_import(handler,admin_id):
    data=_parse_multipart_xlsx(handler);rows=_xlsx_rows(data);valid=[];errors=[];duplicates=0;seen=set()
    with connect_db(_rt().db_path) as db:existing={r[0] for r in db.execute("SELECT id FROM questions")}
    for i,row in enumerate(rows,2):
        try:
            q=_row_question(row)
            if q["id"] in seen or q["id"] in existing:duplicates+=1;errors.append({"row":i,"field":"id","message":"题目ID重复"})
            else:seen.add(q["id"]);valid.append(q)
        except V2Error as exc:errors.append({"row":i,"field":"question","message":exc.message})
    bid=uuid.uuid4().hex
    with connect_db(_rt().db_path) as db:db.execute("INSERT INTO question_import_batches(id,actor_admin_id,file_hash,rows_json,errors_json,created_at) VALUES(?,?,?,?,?,?)",(bid,admin_id,hashlib.sha256(data).hexdigest(),json.dumps(valid,ensure_ascii=False),json.dumps(errors,ensure_ascii=False),int(time.time())))
    return {"batchId":bid,"validRows":len(valid),"duplicateRows":duplicates,"errors":errors}


def _commit_import(admin_id,batch_id,body):
    if body.get("confirm") is not True:raise V2Error("confirmation_required","确认后才能导入")
    with connect_db(_rt().db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            batch=db.execute("SELECT * FROM question_import_batches WHERE id=? AND actor_admin_id=?",(batch_id,admin_id)).fetchone()
            if not batch or batch["status"]!="preview":raise V2Error("import_unavailable","导入批次不存在或已提交")
            errors=json.loads(batch["errors_json"])
            if errors:raise V2Error("import_has_errors","预检存在错误，不能提交")
            rows=json.loads(batch["rows_json"])
            for q in rows:_insert_question(db,admin_id,q)
            db.execute("UPDATE question_import_batches SET status='committed',committed_at=? WHERE id=?",(int(time.time()),batch_id));_rt().service._audit(db,admin_id,"questions.import","question_import",batch_id,True,None,{"count":len(rows)});db.commit()
        except Exception:db.rollback();raise
    return {"batchId":batch_id,"imported":len(rows)}


def _admin_papers(query):
    page,size,offset=_paging(query);q=_q(query,"q");where="WHERE p.deleted_at IS NULL"+(" AND p.title LIKE ?" if q else "");params=[f"%{q}%"] if q else []
    with connect_db(_rt().db_path) as db:
        total=db.execute(f"SELECT COUNT(*) FROM exam_papers p {where}",params).fetchone()[0];rows=db.execute(f"""SELECT p.*,COUNT(i.position) questionCount,(SELECT COUNT(*) FROM exam_attempts a WHERE a.paper_id=p.id) attempts,(SELECT ROUND(AVG(score),1) FROM exam_attempts a WHERE a.paper_id=p.id AND a.status='submitted') averageScore FROM exam_papers p LEFT JOIN exam_paper_items i ON i.paper_id=p.id {where} GROUP BY p.id ORDER BY p.updated_at DESC LIMIT ? OFFSET ?""",(*params,size,offset)).fetchall()
    return {"items":[{"id":r["id"],"title":r["title"],"questionCount":r["questionCount"],"durationMinutes":r["duration_seconds"]//60,"passPercent":r["pass_score"],"attempts":r["attempts"],"averageScore":r["averageScore"],"status":r["status"]} for r in rows],"page":page,"pageSize":size,"total":total}


def _admin_paper_action(admin_id,body):
    action=body.get("action")
    if action in {"publish","unpublish"}:
        if body.get("confirm") is not True:raise V2Error("confirmation_required","试卷状态修改需要确认")
        status="published" if action=="publish" else "unpublished";pid=str(body.get("paperId"))
        with connect_db(_rt().db_path) as db:
            before=db.execute("SELECT status FROM exam_papers WHERE id=? AND deleted_at IS NULL",(pid,)).fetchone()
            if not before:raise V2Error("paper_not_found","试卷不存在")
            db.execute("UPDATE exam_papers SET status=?,updated_at=? WHERE id=?",(status,int(time.time()),pid));_rt().service._audit(db,admin_id,f"paper.{action}","exam_paper",pid,True,{"status":before["status"]},{"status":status})
        return {"id":pid,"status":status}
    if action=="copy":
        pid=str(body.get("paperId"));
        with connect_db(_rt().db_path) as db:
            paper=db.execute("SELECT * FROM exam_papers WHERE id=? AND deleted_at IS NULL",(pid,)).fetchone();items=db.execute("SELECT question_id FROM exam_paper_items WHERE paper_id=? ORDER BY position",(pid,)).fetchall()
        if not paper:raise V2Error("paper_not_found","试卷不存在")
        new=_rt().service.create_exam_paper(admin_id,paper["title"]+"（副本）",[r[0] for r in items],duration_seconds=paper["duration_seconds"],pass_score=paper["pass_score"]);return {"id":new}
    title=str(body.get("title","")).strip();status=str(body.get("status","draft"));count=max(1,min(200,int(body.get("questionCount",20))));rules=body.get("rules") or {};clauses=["status='published'","deleted_at IS NULL"];params=[]
    if not title:raise V2Error("invalid_paper","试卷名称不能为空")
    if status not in {"draft","published"}:raise V2Error("invalid_status","试卷状态无效")
    if not isinstance(rules,dict):raise V2Error("invalid_paper","组卷规则必须是对象")
    if rules.get("topic"):clauses.append("topic=?");params.append(rules["topic"])
    types=rules.get("types")
    if types:
        if isinstance(types,dict): requested_types=[str(key) for key,value in types.items() if int(value)>0]
        elif isinstance(types,list): requested_types=[str(value) for value in types]
        else:raise V2Error("invalid_paper","题型规则无效")
        if not requested_types or not set(requested_types).issubset({"single","multiple","judge"}):raise V2Error("invalid_paper","题型规则无效")
        clauses.append("question_type IN (%s)" % ",".join("?" for _ in requested_types));params.extend(requested_types)
    with connect_db(_rt().db_path) as db:ids=[r[0] for r in db.execute(f"SELECT id FROM questions WHERE {' AND '.join(clauses)} ORDER BY RANDOM() LIMIT ?",(*params,count))]
    if len(ids)<count:raise V2Error("insufficient_questions",f"符合条件的题目仅有 {len(ids)} 道")
    pid=_rt().service.create_exam_paper(admin_id,title,ids,duration_seconds=max(60,int(body.get("durationMinutes",60))*60),pass_score=float(body.get("passPercent",60)),published=status=="published");return {"id":pid,"status":status}


def _admin_feedback(query):
    page,size,offset=_paging(query);status=_q(query,"status");where="WHERE f.status=?" if status else "";params=[status] if status else []
    with connect_db(_rt().db_path) as db:
        total=db.execute(f"SELECT COUNT(*) FROM question_feedback f {where}",params).fetchone()[0];rows=db.execute(f"SELECT f.*,u.display_name FROM question_feedback f JOIN users u ON u.id=f.user_id {where} ORDER BY f.created_at DESC LIMIT ? OFFSET ?",(*params,size,offset)).fetchall()
    return {"items":[{"id":r["id"],"questionId":r["question_id"],"type":r["feedback_type"],"description":r["description"],"status":r["status"],"userDisplayName":r["display_name"],"createdAt":r["created_at"]} for r in rows],"page":page,"pageSize":size,"total":total}


def _admin_patch_feedback(admin_id,body):
    fid=int(body.get("id"));status=str(body.get("status"));
    if status not in {"open","processing","resolved","closed"}:raise V2Error("invalid_status","工单状态无效")
    with connect_db(_rt().db_path) as db:
        before=db.execute("SELECT status FROM question_feedback WHERE id=?",(fid,)).fetchone()
        if not before:raise V2Error("feedback_not_found","纠错工单不存在")
        db.execute("UPDATE question_feedback SET status=?,resolution=?,handled_by=?,updated_at=? WHERE id=?",(status,str(body.get("resolution","")),admin_id,int(time.time()),fid));_rt().service._audit(db,admin_id,"feedback.update","feedback",str(fid),True,dict(before),{"status":status})
    return {"id":fid,"status":status}


def _admin_content(query):
    page,size,offset=_paging(query);ctype=_q(query,"type");where="WHERE content_type=?" if ctype else "";params=[ctype] if ctype else []
    with connect_db(_rt().db_path) as db:total=db.execute(f"SELECT COUNT(*) FROM content_articles {where}",params).fetchone()[0];rows=db.execute(f"SELECT * FROM content_articles {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",(*params,size,offset)).fetchall()
    return {"items":[{"id":r["id"],"type":r["content_type"],"title":r["title"],"body":r["body_text"],"source":r["source"],"status":"archived" if r["status"]=="unpublished" else r["status"],"updatedAt":r["updated_at"]} for r in rows],"page":page,"pageSize":size,"total":total}


def _admin_patch_content(admin_id,body):
    cid=str(body.get("id"));now=int(time.time());status=str(body.get("status","draft"));content_type=str(body.get("type","announcement"));title=str(body.get("title","")).strip();body_text=str(body.get("body","")).strip();db_status="unpublished" if status=="archived" else status
    if content_type not in {"article","announcement","changelog"} or not title or not body_text:raise V2Error("invalid_content","内容类型、标题或正文无效")
    if db_status not in {"draft","published","unpublished"}:raise V2Error("invalid_status","内容状态无效")
    with connect_db(_rt().db_path) as db:
        before=db.execute("SELECT content_type,title,status FROM content_articles WHERE id=?",(cid,)).fetchone()
        if not before:raise V2Error("content_not_found","内容不存在")
        db.execute("UPDATE content_articles SET content_type=?,title=?,body_text=?,source=?,status=?,published_at=CASE WHEN ?='published' THEN COALESCE(published_at,?) ELSE published_at END,updated_at=? WHERE id=?",(content_type,title,body_text,str(body.get("source","")).strip(),db_status,db_status,now,now,cid));_rt().service._audit(db,admin_id,"content.update","content",cid,True,dict(before),{"status":db_status,"title":title})
    return {"id":cid,"status":status}


def _admin_audit(query):
    page,size,offset=_paging(query);q=_q(query,"q");where="WHERE l.action LIKE ? OR l.target_id LIKE ? OR l.request_id LIKE ? OR a.username LIKE ?" if q else "";params=[f"%{q}%"]*4 if q else []
    with connect_db(_rt().db_path) as db:total=db.execute(f"SELECT COUNT(*) FROM admin_audit_logs l LEFT JOIN admin_accounts a ON a.id=l.actor_admin_id {where}",params).fetchone()[0];rows=db.execute(f"SELECT l.*,a.username actor FROM admin_audit_logs l LEFT JOIN admin_accounts a ON a.id=l.actor_admin_id {where} ORDER BY l.created_at DESC LIMIT ? OFFSET ?",(*params,size,offset)).fetchall()
    return {"items":[{"id":r["id"],"actor":r["actor"] or "system","action":r["action"],"target":f"{r['target_type']}:{r['target_id'] or ''}","success":bool(r["success"]),"requestId":r["request_id"],"createdAt":r["created_at"]} for r in rows],"page":page,"pageSize":size,"total":total}
