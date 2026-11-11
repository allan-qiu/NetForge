#!/usr/bin/env python3
"""NetForge multi-user quiz server using only the Python standard library."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import v2_http
from netforge_v2 import V2Error


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "dist"
DATA_DIR = Path(os.environ.get("NETFORGE_DATA_DIR", BASE_DIR / "data"))
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "netforge.db"
HOST = os.environ.get("NETFORGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("NETFORGE_PORT", "8787"))
INITIAL_REGISTRATION_CODE = os.environ.get("NETFORGE_REGISTRATION_CODE", "")
ADMIN_USERNAME = os.environ.get("NETFORGE_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("NETFORGE_ADMIN_PASSWORD", "")
COOKIE_SECURE = os.environ.get("NETFORGE_COOKIE_SECURE", "0") == "1"
SESSION_DAYS = int(os.environ.get("NETFORGE_SESSION_DAYS", "30"))
COOKIE_NAME = "nf_session"
ADMIN_COOKIE_NAME = "nf_admin_session"
MAX_BODY = 64 * 1024
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{3,24}$")


def load_question_bank() -> dict[str, dict]:
    text = (BASE_DIR / "question_bank.js").read_text(encoding="utf-8")
    prefix = "window.NETWORK_QUESTION_BANK = "
    if not text.startswith(prefix):
        raise RuntimeError("questions.js format is invalid")
    payload = json.loads(text[len(prefix):].rstrip().removesuffix(";"))
    return {item["id"]: item for item in payload["questions"]}


QUESTIONS = load_question_bank()


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                last_login_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);

            CREATE TABLE IF NOT EXISTS attempts (
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
            CREATE INDEX IF NOT EXISTS attempts_user_time_idx ON attempts(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS question_state (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                question_id TEXT NOT NULL,
                last_correct INTEGER,
                wrong_count INTEGER NOT NULL DEFAULT 0,
                favorite INTEGER NOT NULL DEFAULT 0,
                last_answer_json TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, question_id)
            );

            CREATE TABLE IF NOT EXISTS user_state (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                active_session_json TEXT,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admin_sessions (
                token_hash TEXT PRIMARY KEY,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS admin_sessions_expiry_idx ON admin_sessions(expires_at);

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );
            """
        )
        db.execute(
            "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES ('registration_code', ?, ?)",
            (INITIAL_REGISTRATION_CODE, int(time.time())),
        )


def get_setting(key: str, default: str = "") -> str:
    with connect_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect_db() as db:
        db.execute(
            """INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (key, value, int(time.time())),
        )


def encode_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(derived).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        method, n, r, p, salt64, digest64 = encoded.split("$")
        if method != "scrypt":
            return False
        salt = base64.b64decode(salt64)
        expected = base64.b64decode(digest64)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class SlidingRateLimit:
    def __init__(self, limit: int, seconds: int):
        self.limit = limit
        self.seconds = seconds
        self.entries: dict[str, list[float]] = {}

    def allowed(self, key: str) -> bool:
        now = time.time()
        recent = [stamp for stamp in self.entries.get(key, []) if now - stamp < self.seconds]
        if len(recent) >= self.limit:
            self.entries[key] = recent
            return False
        recent.append(now)
        self.entries[key] = recent
        if len(self.entries) > 2000:
            self.entries = {k: v for k, v in self.entries.items() if v and now - v[-1] < self.seconds}
        return True


AUTH_LIMIT = SlidingRateLimit(12, 600)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "NetForge/2.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.client_address[0]} - {fmt % args}")

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")

    def _json(self, status: int, payload: dict, extra_headers: dict[str, str] | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("请求长度无效") from exc
        if length <= 0 or length > MAX_BODY:
            raise ValueError("请求内容为空或过大")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求格式无效") from exc
        if not isinstance(value, dict):
            raise ValueError("请求格式无效")
        return value

    def _client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        return forwarded or self.client_address[0]

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        origin_host = urlparse(origin).netloc.lower()
        request_host = self.headers.get("Host", "").lower()
        return hmac.compare_digest(origin_host, request_host)

    def _session_token(self) -> str | None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def _user(self) -> sqlite3.Row | None:
        token = self._session_token()
        if not token:
            return None
        now = int(time.time())
        with connect_db() as db:
            return db.execute(
                """SELECT users.id, users.username, users.display_name
                   FROM sessions JOIN users ON users.id = sessions.user_id
                   WHERE sessions.token_hash = ? AND sessions.expires_at > ?
                     AND users.status = 'active'""",
                (token_digest(token), now),
            ).fetchone()

    def _require_user(self) -> sqlite3.Row | None:
        user = self._user()
        if not user:
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "请先登录"})
        return user

    def _is_admin(self) -> bool:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(ADMIN_COOKIE_NAME)
        if not morsel:
            return False
        now = int(time.time())
        with connect_db() as db:
            row = db.execute(
                """SELECT a.id, a.username, a.display_name, GROUP_CONCAT(ar.role_name) AS roles
                   FROM v2_admin_sessions s
                   JOIN admin_accounts a ON a.id = s.admin_id
                   LEFT JOIN admin_account_roles ar ON ar.admin_id = a.id
                   WHERE s.token_hash = ? AND s.expires_at > ? AND a.status = 'active'
                   GROUP BY a.id""",
                (token_digest(morsel.value), now),
            ).fetchone()
        if row:
            self.v2_admin_id = int(row["id"])
            self.v2_admin = dict(row)
        return bool(row)

    def _require_admin(self) -> bool:
        if not self._is_admin():
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "请先登录管理后台"})
            return False
        return True

    def _set_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        expires = now + SESSION_DAYS * 86400
        with connect_db() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            db.execute(
                "INSERT INTO sessions(token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (token_digest(token), user_id, expires, now),
            )
        parts = [f"{COOKIE_NAME}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={SESSION_DAYS * 86400}"]
        if COOKIE_SECURE:
            parts.append("Secure")
        return "; ".join(parts)

    def _set_admin_session(self, admin_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        expires = now + 12 * 3600
        with connect_db() as db:
            db.execute("DELETE FROM v2_admin_sessions WHERE expires_at <= ?", (now,))
            db.execute(
                "INSERT INTO v2_admin_sessions(token_hash, admin_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (token_digest(token), admin_id, expires, now),
            )
        parts = [f"{ADMIN_COOKIE_NAME}={token}", "Path=/", "HttpOnly", "SameSite=Strict", "Max-Age=43200"]
        if COOKIE_SECURE:
            parts.append("Secure")
        return "; ".join(parts)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_get_api(parsed.path, parse_qs(parsed.query))
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        if not self._same_origin():
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "来源校验失败"})
            return
        path = urlparse(self.path).path
        if v2_http.handle_post(self, path):
            return
        try:
            body = self._body()
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        handlers = {
            "/api/register": self._register,
            "/api/login": self._login,
            "/api/reset-password": self._reset_password_with_token,
            "/api/logout": self._logout,
            "/api/attempts": self._record_attempt,
            "/api/favorites/toggle": self._toggle_favorite,
            "/api/active-session": self._save_active_session,
            "/api/admin/login": self._admin_login,
            "/api/admin/logout": self._admin_logout,
            "/api/admin/users/reset-password": self._admin_reset_password,
            "/api/admin/registration-code": self._admin_update_registration_code,
        }
        handler = handlers.get(path)
        if not handler:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})
            return
        handler(body)

    def do_PATCH(self) -> None:
        if not self._same_origin():
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "来源校验失败"})
            return
        if not v2_http.handle_patch(self, urlparse(self.path).path):
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})

    def do_PUT(self) -> None:
        if not self._same_origin():
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "来源校验失败"})
            return
        if not v2_http.handle_put(self, urlparse(self.path).path):
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})

    def do_DELETE(self) -> None:
        self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "该接口不支持DELETE，请使用已授权的管理操作"}, {"Allow": "GET, POST, PATCH, PUT"})

    def _handle_get_api(self, path: str, query: dict[str, list[str]]) -> None:
        if v2_http.handle_get(self, path, query):
            return
        if path == "/api/health":
            self._json(HTTPStatus.OK, {"ok": True, "questions": len(QUESTIONS)})
            return
        if path == "/api/config":
            now = int(time.time())
            with connect_db() as db:
                has_v2_code = bool(db.execute(
                    """SELECT 1 FROM registration_codes WHERE revoked_at IS NULL
                       AND (expires_at IS NULL OR expires_at > ?)
                       AND (max_uses IS NULL OR used_count < max_uses) LIMIT 1""", (now,),
                ).fetchone())
            self._json(HTTPStatus.OK, {"ok": True, "registrationEnabled": bool(get_setting("registration_code")) or has_v2_code, "questionCount": len(QUESTIONS)})
            return
        if path == "/api/admin/session":
            if not self._require_admin():
                return
            admin = getattr(self, "v2_admin", {})
            roles = [value for value in str(admin.get("roles", "")).split(",") if value]
            role = "super_admin" if "super_admin" in roles else (roles[0] if roles else "")
            self._json(HTTPStatus.OK, {"ok": True, "admin": {
                "username": admin.get("username", ADMIN_USERNAME),
                "displayName": admin.get("display_name", ADMIN_USERNAME),
                "role": role,
                "roles": roles,
            }})
            return
        if path == "/api/admin/dashboard":
            if not self._require_admin():
                return
            self._admin_dashboard()
            return
        user = self._require_user()
        if not user:
            return
        if path == "/api/me":
            self._json(HTTPStatus.OK, {"ok": True, "user": dict(user)})
        elif path == "/api/questions":
            self._json(HTTPStatus.OK, {"ok": True, "questions": [
                {
                    "id": question["id"],
                    "type": question["type"],
                    "topic": question["topic"],
                    "question": question["question"],
                    "options": question["options"],
                    "needsReview": bool(question.get("needsReview")),
                }
                for question in QUESTIONS.values()
            ]})
        elif path == "/api/state":
            self._get_state(user)
        elif path == "/api/attempts":
            limit = min(max(int(query.get("limit", ["100"])[0]), 1), 200)
            self._get_attempts(user, limit)
        elif path == "/api/active-session":
            with connect_db() as db:
                row = db.execute("SELECT active_session_json FROM user_state WHERE user_id = ?", (user["id"],)).fetchone()
            active = json.loads(row["active_session_json"]) if row and row["active_session_json"] else None
            self._json(HTTPStatus.OK, {"ok": True, "activeSession": active})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})

    def _register(self, body: dict) -> None:
        registration_code = get_setting("registration_code")
        code = str(body.get("registrationCode", ""))
        now = int(time.time())
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        with connect_db() as db:
            v2_code = db.execute(
                """SELECT * FROM registration_codes WHERE code_hash = ? AND revoked_at IS NULL
                   AND (expires_at IS NULL OR expires_at > ?)
                   AND (max_uses IS NULL OR used_count < max_uses)""",
                (code_hash, now),
            ).fetchone()
        if not registration_code and not v2_code:
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "当前未开放注册"})
            return
        if not AUTH_LIMIT.allowed(self._client_ip()):
            self._json(HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": "尝试次数过多，请稍后再试"})
            return
        username = str(body.get("username", "")).strip()
        display_name = str(body.get("displayName", "")).strip() or username
        password = str(body.get("password", ""))
        if not v2_code and not hmac.compare_digest(code, registration_code):
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "注册码不正确"})
            return
        if not USERNAME_RE.fullmatch(username):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "用户名需为3至24位中文、字母、数字、横线或下划线"})
            return
        if not 1 <= len(display_name) <= 30:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "昵称长度需为1至30个字符"})
            return
        if len(password) < 8 or len(password) > 128:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "密码长度需为8至128个字符"})
            return
        try:
            with connect_db() as db:
                db.execute("BEGIN IMMEDIATE")
                cursor = db.execute(
                    "INSERT INTO users(username, display_name, password_hash, created_at, last_login_at) VALUES (?, ?, ?, ?, ?)",
                    (username, display_name, encode_password(password), now, now),
                )
                user_id = cursor.lastrowid
                if v2_code:
                    active_code = db.execute(
                        """SELECT id FROM registration_codes WHERE id = ? AND revoked_at IS NULL
                           AND (expires_at IS NULL OR expires_at > ?)
                           AND (max_uses IS NULL OR used_count < max_uses)""",
                        (v2_code["id"], now),
                    ).fetchone()
                    if not active_code:
                        raise V2Error("invalid_registration_code", "注册码无效或已过期")
                    db.execute(
                        "INSERT INTO registration_code_uses(code_id, user_id, used_at) VALUES (?, ?, ?)",
                        (v2_code["id"], user_id, now),
                    )
                    db.execute("UPDATE registration_codes SET used_count = used_count + 1 WHERE id = ?", (v2_code["id"],))
                db.commit()
        except sqlite3.IntegrityError:
            self._json(HTTPStatus.CONFLICT, {"ok": False, "error": "该用户名已经存在"})
            return
        except V2Error as exc:
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": exc.message})
            return
        cookie = self._set_session(int(user_id))
        self._json(HTTPStatus.CREATED, {"ok": True, "user": {"id": user_id, "username": username, "display_name": display_name}}, {"Set-Cookie": cookie})

    def _login(self, body: dict) -> None:
        if not AUTH_LIMIT.allowed(self._client_ip()):
            self._json(HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": "尝试次数过多，请稍后再试"})
            return
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        with connect_db() as db:
            user = db.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
        if not user or user["status"] != "active" or not verify_password(password, user["password_hash"]):
            time.sleep(0.25)
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "用户名或密码不正确"})
            return
        now = int(time.time())
        with connect_db() as db:
            db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user["id"]))
        cookie = self._set_session(user["id"])
        self._json(HTTPStatus.OK, {"ok": True, "user": {"id": user["id"], "username": user["username"], "display_name": user["display_name"]}}, {"Set-Cookie": cookie})

    def _reset_password_with_token(self, body: dict) -> None:
        if not AUTH_LIMIT.allowed("reset:" + self._client_ip()):
            self._json(HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": "尝试次数过多，请稍后再试"})
            return
        username = str(body.get("username", "")).strip()
        token = str(body.get("token", "")).strip()
        password = str(body.get("newPassword", ""))
        if len(password) < 8 or len(password) > 128 or not token:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "重置凭据或新密码格式无效"})
            return
        now = int(time.time())
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with connect_db() as db:
            row = db.execute(
                """SELECT t.id, t.user_id FROM password_reset_tokens t
                   JOIN users u ON u.id=t.user_id
                   WHERE u.username=? COLLATE NOCASE AND u.status='active'
                     AND t.token_hash=? AND t.used_at IS NULL AND t.expires_at>?""",
                (username, digest, now),
            ).fetchone()
            if not row:
                time.sleep(0.25)
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "重置凭据无效或已过期"})
                return
            db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (encode_password(password), now, row["user_id"]))
            db.execute("UPDATE password_reset_tokens SET used_at=? WHERE id=?", (now, row["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
        self._json(HTTPStatus.OK, {"ok": True, "message": "密码已重置，请重新登录"})

    def _logout(self, _body: dict) -> None:
        token = self._session_token()
        if token:
            with connect_db() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(token),))
        parts = [f"{COOKIE_NAME}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
        if COOKIE_SECURE:
            parts.append("Secure")
        self._json(HTTPStatus.OK, {"ok": True}, {"Set-Cookie": "; ".join(parts)})

    def _admin_login(self, body: dict) -> None:
        if not AUTH_LIMIT.allowed("admin:" + self._client_ip()):
            self._json(HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": "尝试次数过多，请稍后再试"})
            return
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        with connect_db() as db:
            admin = db.execute(
                "SELECT * FROM admin_accounts WHERE username = ? COLLATE NOCASE AND status = 'active'",
                (username,),
            ).fetchone()
        if not admin or not verify_password(password, admin["password_hash"]):
            time.sleep(0.35)
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "管理员账号或密码不正确"})
            return
        now = int(time.time())
        with connect_db() as db:
            db.execute("UPDATE admin_accounts SET last_login_at = ? WHERE id = ?", (now, admin["id"]))
            roles = [row["role_name"] for row in db.execute("SELECT role_name FROM admin_account_roles WHERE admin_id = ?", (admin["id"],))]
        cookie = self._set_admin_session(int(admin["id"]))
        role = "super_admin" if "super_admin" in roles else (roles[0] if roles else "")
        self._json(HTTPStatus.OK, {"ok": True, "admin": {
            "username": admin["username"], "displayName": admin["display_name"], "role": role, "roles": roles,
        }}, {"Set-Cookie": cookie})

    def _admin_logout(self, _body: dict) -> None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(ADMIN_COOKIE_NAME)
        if morsel:
            with connect_db() as db:
                db.execute("DELETE FROM v2_admin_sessions WHERE token_hash = ?", (token_digest(morsel.value),))
        parts = [f"{ADMIN_COOKIE_NAME}=", "Path=/", "HttpOnly", "SameSite=Strict", "Max-Age=0"]
        if COOKIE_SECURE:
            parts.append("Secure")
        self._json(HTTPStatus.OK, {"ok": True}, {"Set-Cookie": "; ".join(parts)})

    def _admin_dashboard(self) -> None:
        now = int(time.time())
        with connect_db() as db:
            totals = db.execute(
                """SELECT
                     (SELECT COUNT(*) FROM users) AS users,
                     (SELECT COUNT(*) FROM attempts WHERE skipped = 0) AS answered,
                     (SELECT COALESCE(SUM(correct), 0) FROM attempts WHERE skipped = 0) AS correct,
                     (SELECT COUNT(DISTINCT user_id) FROM attempts WHERE created_at >= ?) AS active_users""",
                (now - 7 * 86400,),
            ).fetchone()
            users = db.execute(
                """WITH attempt_stats AS (
                       SELECT user_id, COUNT(*) FILTER (WHERE skipped = 0) AS answered,
                              COALESCE(SUM(correct) FILTER (WHERE skipped = 0), 0) AS correct,
                              MAX(created_at) AS last_activity
                       FROM attempts GROUP BY user_id
                   ), state_stats AS (
                       SELECT user_id,
                              COALESCE(SUM(CASE WHEN last_correct = 0 THEN 1 ELSE 0 END), 0) AS wrong,
                              COALESCE(SUM(favorite), 0) AS favorites
                       FROM question_state GROUP BY user_id
                   )
                   SELECT u.id, u.username, u.display_name, u.created_at, u.last_login_at,
                          COALESCE(a.answered, 0) AS answered, COALESCE(a.correct, 0) AS correct,
                          COALESCE(s.wrong, 0) AS wrong, COALESCE(s.favorites, 0) AS favorites,
                          a.last_activity
                   FROM users u
                   LEFT JOIN attempt_stats a ON a.user_id = u.id
                   LEFT JOIN state_stats s ON s.user_id = u.id
                   ORDER BY COALESCE(a.last_activity, u.last_login_at, u.created_at) DESC"""
            ).fetchall()
            topics = db.execute(
                """SELECT topic, COUNT(*) AS answered, COALESCE(SUM(correct), 0) AS correct,
                          COUNT(DISTINCT user_id) AS users
                   FROM attempts WHERE skipped = 0 GROUP BY topic ORDER BY answered DESC"""
            ).fetchall()
            daily = db.execute(
                """SELECT date(created_at, 'unixepoch', '+8 hours') AS day,
                          COUNT(*) AS answered, COALESCE(SUM(correct), 0) AS correct
                   FROM attempts WHERE skipped = 0 AND created_at >= ?
                   GROUP BY day ORDER BY day""",
                (now - 13 * 86400,),
            ).fetchall()
        self._json(HTTPStatus.OK, {
            "ok": True,
            "registrationCode": get_setting("registration_code"),
            "totals": dict(totals),
            "users": [dict(row) for row in users],
            "topics": [dict(row) for row in topics],
            "daily": [dict(row) for row in daily],
        })

    def _admin_reset_password(self, body: dict) -> None:
        if not self._require_admin():
            return
        admin_id=int(getattr(self,'v2_admin_id',v2_http._rt().bootstrap_admin_id))
        if not v2_http._rt().service.has_permission(admin_id,'users.password'):
            self._json(HTTPStatus.FORBIDDEN,{"ok":False,"error":"当前角色无权修改用户密码"})
            return
        try:
            user_id = int(body.get("userId"))
        except (TypeError, ValueError):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "用户编号无效"})
            return
        temporary_password = "NF-" + secrets.token_urlsafe(9)
        with connect_db() as db:
            user = db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "用户不存在"})
                return
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (encode_password(temporary_password), user_id))
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self._json(HTTPStatus.OK, {
            "ok": True,
            "username": user["username"],
            "temporaryPassword": temporary_password,
        })

    def _admin_update_registration_code(self, body: dict) -> None:
        if not self._require_admin():
            return
        code = str(body.get("registrationCode", "")).strip()
        if not 6 <= len(code) <= 64 or any(char.isspace() for char in code):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "注册码需为6至64位且不能包含空格"})
            return
        set_setting("registration_code", code)
        self._json(HTTPStatus.OK, {"ok": True, "registrationCode": code})

    def _record_attempt(self, body: dict) -> None:
        user = self._require_user()
        if not user:
            return
        question_id = str(body.get("questionId", ""))
        question = QUESTIONS.get(question_id)
        if not question:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "题目不存在"})
            return
        selected = body.get("selected", [])
        skipped = bool(body.get("skipped", False))
        if not isinstance(selected, list) or len(selected) > 8 or any(not isinstance(item, str) for item in selected):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "答案格式无效"})
            return
        selected = sorted(set(selected))
        correct = not skipped and selected == sorted(question["answer"])
        now = int(time.time())
        with connect_db() as db:
            db.execute(
                "INSERT INTO attempts(user_id, question_id, selected_json, correct, skipped, topic, question_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user["id"], question_id, json.dumps(selected), int(correct), int(skipped), question["topic"], question["type"], now),
            )
            if not skipped:
                db.execute(
                    """INSERT INTO question_state(user_id, question_id, last_correct, wrong_count, favorite, last_answer_json, updated_at)
                       VALUES (?, ?, ?, ?, 0, ?, ?)
                       ON CONFLICT(user_id, question_id) DO UPDATE SET
                         last_correct = excluded.last_correct,
                         wrong_count = question_state.wrong_count + CASE WHEN excluded.last_correct = 0 THEN 1 ELSE 0 END,
                         last_answer_json = excluded.last_answer_json,
                         updated_at = excluded.updated_at""",
                    (user["id"], question_id, int(correct), 0 if correct else 1, json.dumps(selected), now),
                )
        self._json(HTTPStatus.CREATED, {
            "ok": True,
            "correct": correct,
            "answer": question["answer"],
            "explanation": question.get("explanation", ""),
            "source": question.get("source", ""),
            "references": question.get("references", []),
            "explanationConfidence": question.get("explanationConfidence", "medium"),
            "needsReview": bool(question.get("needsReview")),
        })

    def _toggle_favorite(self, body: dict) -> None:
        user = self._require_user()
        if not user:
            return
        question_id = str(body.get("questionId", ""))
        if question_id not in QUESTIONS:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "题目不存在"})
            return
        now = int(time.time())
        with connect_db() as db:
            row = db.execute("SELECT favorite FROM question_state WHERE user_id = ? AND question_id = ?", (user["id"], question_id)).fetchone()
            favorite = 0 if row and row["favorite"] else 1
            db.execute(
                """INSERT INTO question_state(user_id, question_id, last_correct, wrong_count, favorite, updated_at)
                   VALUES (?, ?, NULL, 0, ?, ?)
                   ON CONFLICT(user_id, question_id) DO UPDATE SET favorite = excluded.favorite, updated_at = excluded.updated_at""",
                (user["id"], question_id, favorite, now),
            )
        self._json(HTTPStatus.OK, {"ok": True, "favorite": bool(favorite)})

    def _save_active_session(self, body: dict) -> None:
        user = self._require_user()
        if not user:
            return
        active = body.get("activeSession")
        encoded = json.dumps(active, ensure_ascii=False, separators=(",", ":")) if active is not None else None
        if encoded and len(encoded.encode("utf-8")) > 32 * 1024:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "训练进度数据过大"})
            return
        now = int(time.time())
        with connect_db() as db:
            db.execute(
                """INSERT INTO user_state(user_id, active_session_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET active_session_json = excluded.active_session_json, updated_at = excluded.updated_at""",
                (user["id"], encoded, now),
            )
        self._json(HTTPStatus.OK, {"ok": True})

    def _get_state(self, user: sqlite3.Row) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
        with connect_db() as db:
            totals = db.execute(
                """SELECT COUNT(*) AS answered, COALESCE(SUM(correct), 0) AS correct
                   FROM attempts WHERE user_id = ? AND skipped = 0""",
                (user["id"],),
            ).fetchone()
            today_count = db.execute(
                """SELECT COUNT(*) AS count FROM attempts
                   WHERE user_id = ? AND skipped = 0 AND date(created_at, 'unixepoch', '+8 hours') = ?""",
                (user["id"], today),
            ).fetchone()["count"]
            states = db.execute(
                "SELECT question_id, last_correct, favorite FROM question_state WHERE user_id = ?",
                (user["id"],),
            ).fetchall()
        wrong_ids = [row["question_id"] for row in states if row["last_correct"] == 0]
        favorites = [row["question_id"] for row in states if row["favorite"] == 1]
        self._json(HTTPStatus.OK, {
            "ok": True,
            "progress": {
                "answered": totals["answered"],
                "correct": totals["correct"],
                "wrongIds": wrong_ids,
                "favoriteIds": favorites,
                "today": {today.replace("-", "/"): today_count},
                "history": {},
            },
        })

    def _get_attempts(self, user: sqlite3.Row, limit: int) -> None:
        with connect_db() as db:
            rows = db.execute(
                """SELECT id, question_id, selected_json, correct, skipped, topic, question_type, created_at
                   FROM attempts WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
                (user["id"], limit),
            ).fetchall()
        attempts = []
        for row in rows:
            question = QUESTIONS.get(row["question_id"], {})
            revealed = not bool(row["skipped"])
            attempts.append({
                "id": row["id"],
                "questionId": row["question_id"],
                "question": question.get("question", "题目已更新"),
                "selected": json.loads(row["selected_json"]),
                "answer": question.get("answer", []) if revealed else [],
                "explanation": question.get("explanation", "") if revealed else "",
                "source": question.get("source", "") if revealed else "",
                "needsReview": bool(question.get("needsReview")) if revealed else False,
                "correct": bool(row["correct"]),
                "skipped": bool(row["skipped"]),
                "topic": row["topic"],
                "type": row["question_type"],
                "createdAt": row["created_at"],
            })
        self._json(HTTPStatus.OK, {"ok": True, "attempts": attempts})

    def _serve_static(self, request_path: str) -> None:
        page_aliases = {
            "/": "v2.html",
            "/admin": "admin-v2.html",
            "/admin/": "admin-v2.html",
            "/admin.html": "admin-v2.html",
            "/legacy.html": "index.html",
        }
        relative = page_aliases.get(request_path, request_path.lstrip("/") or "v2.html")
        root = UPLOADS_DIR.resolve() if request_path.startswith("/uploads/") else STATIC_DIR.resolve()
        if request_path.startswith("/uploads/"):
            relative = request_path.removeprefix("/uploads/")
        candidate = (root / relative).resolve()
        status = HTTPStatus.OK
        try:
            candidate.relative_to(root)
        except ValueError:
            candidate = STATIC_DIR / "not-found.html"
            status = HTTPStatus.NOT_FOUND
        if not candidate.is_file():
            candidate = STATIC_DIR / "not-found.html"
            status = HTTPStatus.NOT_FOUND
        data = candidate.read_bytes()
        mime = "application/javascript" if candidate.suffix == ".js" else (mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
        self.send_response(status)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or mime in {"application/javascript", "application/json"} else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    initialize_db()
    v2_http.configure(
        DB_PATH,
        BASE_DIR / "question_bank.js",
        ADMIN_USERNAME,
        ADMIN_PASSWORD,
        DATA_DIR / "backups",
    )
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"NetForge serving on http://{HOST}:{PORT} with {len(QUESTIONS)} questions")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
