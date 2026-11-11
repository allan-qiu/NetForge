import base64
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HttpClient:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()

    def upload(self, path, field, filename, content, content_type):
        boundary = "----NetForgeIntegrationBoundary"
        data = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("ascii") + content + f"\r\n--{boundary}--\r\n".encode("ascii")
        request = urllib.request.Request(self.base + path, data=data, method="POST")
        request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()


class V2HttpIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        env = os.environ.copy()
        env.update({
            "NETFORGE_DATA_DIR": cls.temp.name,
            "NETFORGE_HOST": "127.0.0.1",
            "NETFORGE_PORT": str(cls.port),
            "NETFORGE_REGISTRATION_CODE": "INTEGRATION-2026",
            "NETFORGE_ADMIN_USERNAME": "admin",
            "NETFORGE_ADMIN_PASSWORD": "Integration-Admin-2026!",
        })
        cls.process = subprocess.Popen(
            [sys.executable, "server.py"], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cls.base = f"http://127.0.0.1:{cls.port}"
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(cls.base + "/api/health", timeout=1) as response:
                    if response.status == 200:
                        cls.question_count = json.load(response)['questions']
                        return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("test server failed to start")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.process.kill()
        cls.temp.cleanup()

    def test_memorize_options_and_personal_analysis_routes_are_user_scoped(self):
        user,other=HttpClient(self.base),HttpClient(self.base)
        # Distinct simulated clients keep this scenario independent of the shared auth limiter.
        user.opener.addheaders=[('X-Forwarded-For','198.51.100.41')]
        other.opener.addheaders=[('X-Forwarded-For','198.51.100.42')]
        for client,name in ((user,'study_upgrade_user'),(other,'study_upgrade_other')):
            self.assertEqual(client.request('/api/register','POST',{'username':name,'displayName':'背题验收','password':'Study-Upgrade-2026!','registrationCode':'INTEGRATION-2026'})[0],201)
        anonymous=HttpClient(self.base)
        self.assertEqual(anonymous.request('/api/v2/me/ai-summary')[0],401)
        self.assertEqual(anonymous.request('/api/v2/me/ai-summary','POST',{'confirm':True})[0],401)
        self.assertFalse(user.request('/api/v2/me/ai-summary')[1]['data']['ready'])
        self.assertEqual(user.request('/api/v2/me/ai-summary','POST',{})[0],400)
        self.assertEqual(user.request('/api/v2/me/ai-summary','POST',{'confirm':True,'userId':1})[0],400)
        for count in (20,60,80,'all'):
            status,data=user.request('/api/v2/practice-sessions','POST',{'mode':'study','count':count})
            self.assertEqual(status,201)
            session=data['data']['session'];ids=session['questionIds']
            self.assertEqual(len(ids),self.question_count if count=='all' else min(count, self.question_count))
            self.assertEqual(len(set(ids)),len(ids))
        qid=ids[0];path=f"/api/v2/practice-sessions/{session['id']}/answers"
        self.assertEqual(user.request(path,'POST',{'questionId':qid,'studyReveal':True})[0],201)
        self.assertEqual(other.request('/api/v2/ai/explain','POST',{'questionId':qid,'sessionId':session['id']})[0],403)
        self.assertEqual(user.request(path,'POST',{'questionId':qid,'selected':['A'],'submissionId':'fake'})[0],400)
        self.assertEqual(user.request('/api/v2/home')[1]['data']['metrics']['answered'],0)
        self.assertEqual(user.request('/api/v2/practice-sessions','POST',{'mode':'study','count':100})[0],400)

    def test_end_to_end_practice_exam_admin_and_session_revocation(self):
        user = HttpClient(self.base)
        status, registered = user.request("/api/register", "POST", {
            "username": "integration_user", "displayName": "集成用户",
            "password": "Integration-User-2026!", "registrationCode": "INTEGRATION-2026",
        })
        self.assertEqual(status, 201)
        user_id = registered["user"]["id"]

        status, bank = user.request("/api/v2/questions?page=1&pageSize=1")
        self.assertEqual(status, 200)
        public_question = bank["data"]["items"][0]
        for secret_field in ("answer", "explanation", "references", "explanationConfidence"):
            self.assertNotIn(secret_field, public_question)
        for _ in range(3):
            self.assertEqual(user.request("/api/attempts", "POST", {
                "questionId": public_question["id"], "selected": [], "skipped": False,
            })[0], 201)

        status, created = user.request("/api/v2/practice-sessions", "POST", {"mode": "practice", "count": 1})
        self.assertEqual(status, 201)
        session = created["data"]["session"]
        question_id = session["questionIds"][0]
        answer_body = {"questionId": question_id, "selected": ["A"], "submissionId": "same-request"}
        first = user.request(f"/api/v2/practice-sessions/{session['id']}/answers", "POST", answer_body)[1]["data"]
        second = user.request(f"/api/v2/practice-sessions/{session['id']}/answers", "POST", answer_body)[1]["data"]
        self.assertEqual(first["answerId"], second["answerId"])
        self.assertIn("explanation", first)
        self.assertIn("references", first)
        self.assertIn("explanationConfidence", first)

        before = user.request("/api/v2/home")[1]["data"]["metrics"]["answered"]
        study = user.request("/api/v2/practice-sessions", "POST", {"mode": "study", "count": 20})[1]["data"]["session"]
        reveal = user.request(f"/api/v2/practice-sessions/{study['id']}/answers", "POST", {
            "questionId": study["questionIds"][0], "selected": [], "submissionId": "reveal", "studyReveal": True,
        })[1]["data"]
        self.assertTrue(reveal["studyReveal"])
        after = user.request("/api/v2/home")[1]["data"]["metrics"]["answered"]
        self.assertEqual(before, after)

        status, exam = user.request("/api/v2/exam-attempts", "POST", {
            "configuration": {"questionCount": 3, "durationMinutes": 5, "passScore": 60}
        })
        self.assertEqual(status, 201)
        self.assertEqual(len(exam["data"]["attempt"]["questions"]), 3)

        admin = HttpClient(self.base)
        self.assertEqual(admin.request("/api/admin/login", "POST", {
            "username": "admin", "password": "Integration-Admin-2026!",
        })[0], 200)
        dashboard = admin.request("/api/v2/admin/dashboard")[1]["data"]
        self.assertGreaterEqual(dashboard["metrics"]["users"], 1)
        self.assertGreaterEqual(dashboard["totals"]["answers"], 4)
        self.assertTrue(any(item["questionId"] == public_question["id"] for item in dashboard["riskSignals"]))
        user_row = admin.request("/api/v2/admin/users?q=integration_user")[1]["data"]["items"][0]
        self.assertEqual(user_row["practiceAnswers"], 4)
        reset = admin.request("/api/v2/admin/users", "POST", {
            "action": "reset-token", "userId": user_id, "confirm": True,
        })[1]["data"]
        reset_client = HttpClient(self.base)
        self.assertEqual(reset_client.request("/api/reset-password", "POST", {
            "username": "integration_user", "token": reset["token"], "newPassword": "Integration-New-2026!",
        })[0], 200)
        self.assertEqual(reset_client.request("/api/reset-password", "POST", {
            "username": "integration_user", "token": reset["token"], "newPassword": "Integration-Newer-2026!",
        })[0], 400)
        self.assertEqual(user.request("/api/v2/home")[0], 401)
        self.assertEqual(user.request("/api/login", "POST", {
            "username": "integration_user", "password": "Integration-New-2026!",
        })[0], 200)
        status, disabled = admin.request("/api/v2/admin/users", "POST", {
            "action": "disable", "userId": user_id, "reason": "integration test", "confirm": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(disabled["data"]["action"], "disable")
        self.assertEqual(user.request("/api/v2/home")[0], 401)

    def test_learning_upgrade_routes_permissions_and_deletion(self):
        user=HttpClient(self.base);admin=HttpClient(self.base)
        self.assertEqual(user.request('/api/register','POST',{'username':'upgrade_routes','displayName':'升级测试','password':'Upgrade-Routes-2026!','registrationCode':'INTEGRATION-2026'})[0],201)
        uid=user.request('/api/v2/me/profile')[1]['data']['id']
        self.assertEqual(admin.request('/api/admin/login','POST',{'username':'admin','password':'Integration-Admin-2026!'})[0],200)
        appearance={'mode':'light','palette':'rose','background':'#fafafa','surface':'#ffffff'}
        self.assertEqual(user.request('/api/v2/me/appearance','PATCH',appearance)[0],200)
        self.assertEqual(user.request('/api/v2/me/appearance')[1]['data'],appearance)
        self.assertEqual(admin.request('/api/v2/admin/me/appearance')[1]['data']['mode'],'dark')
        self.assertEqual(user.request(f'/api/v2/admin/users/{uid}')[0],401)
        detail=admin.request(f'/api/v2/admin/users/{uid}')[1]['data']
        self.assertEqual(detail['user']['username'],'upgrade_routes')
        self.assertNotIn('password_hash',json.dumps(detail))
        self.assertEqual(user.request('/api/v2/ai/explain','POST',{'questionId':'net-0001'})[0],403)
        self.assertEqual(user.request('/api/v2/reference-document','POST',{'questionId':'net-0001'})[0],403)
        self.assertEqual(admin.request('/api/v2/admin/admins','POST',{'username':'upgrade_support','password':'Upgrade-Support-2026!','roles':['support_admin']})[0],201)
        support=HttpClient(self.base)
        self.assertEqual(support.request('/api/admin/login','POST',{'username':'upgrade_support','password':'Upgrade-Support-2026!'})[0],200)
        self.assertEqual(support.request(f'/api/v2/admin/users/{uid}')[0],200)
        self.assertEqual(support.request(f'/api/v2/admin/users/{uid}/ai-portrait','POST',{'confirm':True})[0],403)
        deletion={'action':'hard-delete','userId':uid,'confirm':True,'confirmUsername':'upgrade_routes'}
        self.assertEqual(support.request('/api/v2/admin/users','POST',deletion)[0],403)
        self.assertEqual(admin.request('/api/v2/admin/users','POST',{**deletion,'confirm':False})[0],400)
        self.assertEqual(admin.request('/api/v2/admin/users','POST',deletion)[0],200)
        self.assertEqual(user.request('/api/v2/home')[0],401)
        self.assertEqual(admin.request(f'/api/v2/admin/users/{uid}')[0],404)
        self.assertEqual(admin.request('/api/health','DELETE')[0],405)

    def test_admin_user_edit_and_password_http_permissions(self):
        user=HttpClient(self.base);admin=HttpClient(self.base)
        status,registered=user.request('/api/register','POST',{'username':'editable_user','displayName':'可编辑用户','password':'Editable-User-2026!','registrationCode':'INTEGRATION-2026'})
        self.assertEqual(status,201);uid=registered['user']['id']
        self.assertEqual(admin.request('/api/admin/login','POST',{'username':'admin','password':'Integration-Admin-2026!'})[0],200)
        admin.request('/api/v2/admin/admins','POST',{'username':'edit_support','password':'Edit-Support-2026!','roles':['support_admin']})
        support=HttpClient(self.base);support.request('/api/admin/login','POST',{'username':'edit_support','password':'Edit-Support-2026!'})
        detail=admin.request(f'/api/v2/admin/users/{uid}')[1]['data']
        body={'profile':{'display_name':'新的昵称'},'metrics':{'answered':45,'correct':30,'wrong':5,'exams':4,'submittedExams':3,'favorites':7,'feedback':2},'reason':'统计修正','confirm':True,'version':detail['editVersion']}
        self.assertEqual(user.request(f'/api/v2/admin/users/{uid}','PATCH',body)[0],401)
        self.assertEqual(support.request(f'/api/v2/admin/users/{uid}','PATCH',body)[0],403)
        self.assertEqual(support.request('/api/admin/users/reset-password','POST',{'userId':uid})[0],403)
        self.assertEqual(admin.request(f'/api/v2/admin/users/{uid}','PATCH',{**body,'profile':{'created_at':1}})[0],400)
        status,result=admin.request(f'/api/v2/admin/users/{uid}','PATCH',body)
        self.assertEqual(status,200);self.assertEqual(result['data']['metrics']['answered'],45)
        self.assertEqual(admin.request(f'/api/v2/admin/users/{uid}','PATCH',body)[0],409)
        password={'password':'Replacement-2026!','reason':'用户申请','confirm':True,'version':result['data']['editVersion']}
        self.assertEqual(support.request(f'/api/v2/admin/users/{uid}/password','POST',password)[0],403)
        self.assertEqual(admin.request(f'/api/v2/admin/users/{uid}/password','POST',password)[0],200)
        self.assertEqual(user.request('/api/v2/home')[0],401)
        self.assertEqual(user.request('/api/login','POST',{'username':'editable_user','password':'Editable-User-2026!'})[0],401)
        self.assertEqual(user.request('/api/login','POST',{'username':'editable_user','password':'Replacement-2026!'})[0],200)
        self.assertEqual(user.request('/api/v2/home')[1]['data']['metrics']['answered'],45)
        latest=admin.request(f'/api/v2/admin/users/{uid}')[1]['data']
        renamed={**body,'profile':{'username':'renamed_learner'},'metrics':{},'version':latest['editVersion']}
        self.assertEqual(admin.request(f'/api/v2/admin/users/{uid}','PATCH',renamed)[0],200)
        self.assertEqual(user.request('/api/v2/home')[0],401)
        self.assertEqual(user.request('/api/login','POST',{'username':'renamed_learner','password':'Replacement-2026!'})[0],200)
        self.assertEqual(admin.request('/api/v2/admin/users/99999999','PATCH',renamed)[0],404)

    def test_admin_short_routes_serve_the_management_console(self):
        for path in ("/admin", "/admin/"):
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                html = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("V2 ADMIN CONSOLE", html)
        with urllib.request.urlopen(self.base + "/admin-v2.js?v=2.3.1", timeout=10) as response:
            self.assertEqual(response.headers.get_content_type(), "application/javascript")
        with urllib.request.urlopen(self.base + "/ui-system.css?v=2.3.1", timeout=10) as response:
            self.assertEqual(response.headers.get_content_type(), "text/css")

    def test_profile_avatar_public_summary_and_seeded_changelog(self):
        user = HttpClient(self.base)
        self.assertEqual(user.request("/api/register", "POST", {
            "username": "profile_user", "displayName": "资料用户",
            "password": "Profile-User-2026!", "registrationCode": "INTEGRATION-2026",
        })[0], 201)
        profile = user.request("/api/v2/me/profile")[1]["data"]
        user_id = profile["id"]

        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        status, uploaded = user.upload("/api/v2/me/avatar", "avatar", "avatar.png", png, "image/png")
        self.assertEqual(status, 201)
        avatar_url = uploaded["data"]["avatarUrl"]
        self.assertTrue(avatar_url.startswith("/uploads/avatars/user-"))
        with user.opener.open(self.base + avatar_url, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertEqual(response.read(), png)
        self.assertEqual(user.upload("/api/v2/me/avatar", "avatar", "avatar.txt", b"not-an-image", "text/plain")[0], 400)

        status, updated = user.request("/api/v2/me/profile", "PATCH", {
            "displayName": "资料用户更新", "avatarUrl": "https://example.invalid/ignored.png",
        })
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["avatarUrl"], avatar_url)
        public = user.request(f"/api/v2/users/{user_id}/profile")[1]["data"]
        self.assertEqual(public["avatarUrl"], avatar_url)
        self.assertEqual(public["username"], "profile_user")
        self.assertTrue({"practiceAnswered", "accuracy", "averageDurationSeconds"}.issubset(public))
        for private_field in ("password_hash", "lastLoginAt", "status", "leaderboardOptIn", "createdAt"):
            self.assertNotIn(private_field, public)

        leaderboard = user.request("/api/v2/leaderboard?period=all")[1]["data"]["items"]
        self.assertTrue(any(item["userId"] == user_id for item in leaderboard))
        changelog = user.request("/api/v2/content?type=changelog&pageSize=200")[1]["data"]
        self.assertGreaterEqual(changelog["total"], 4)
        self.assertTrue(all(item["body"] for item in changelog["items"]))

    def test_direct_practice_continues_and_completes_with_owned_session(self):
        user = HttpClient(self.base)
        self.assertEqual(user.request("/api/register", "POST", {
            "username": "direct_practice", "displayName": "连续练习用户",
            "password": "Direct-Practice-2026!", "registrationCode": "INTEGRATION-2026",
        })[0], 201)
        status, created = user.request("/api/v2/practice-sessions", "POST", {
            "mode": "practice", "count": 2,
        })
        self.assertEqual(status, 201)
        session = created["data"]["session"]
        self.assertEqual(len(session["questions"]), 2)

        for index, question in enumerate(session["questions"]):
            status, _ = user.request(f"/api/v2/practice-sessions/{session['id']}/answers", "POST", {
                "questionId": question["id"], "selected": [question["options"][0]["key"]],
                "submissionId": f"direct-{index}",
            })
            self.assertEqual(status, 201)
            if index + 1 < len(session["questions"]):
                self.assertEqual(user.request(f"/api/v2/practice-sessions/{session['id']}", "PATCH", {
                    "status": "active", "currentIndex": index + 1,
                })[0], 200)

        self.assertEqual(user.request(f"/api/v2/practice-sessions/{session['id']}", "PATCH", {
            "status": "completed", "currentIndex": len(session["questions"]) - 1,
        })[0], 200)
        self.assertIsNone(user.request("/api/v2/home")[1]["data"]["activePractice"])

        other = HttpClient(self.base)
        self.assertEqual(other.request("/api/register", "POST", {
            "username": "foreign_practice", "displayName": "其他练习用户",
            "password": "Foreign-Practice-2026!", "registrationCode": "INTEGRATION-2026",
        })[0], 201)
        self.assertEqual(other.request(f"/api/v2/practice-sessions/{session['id']}")[0], 404)
        self.assertEqual(HttpClient(self.base).request("/api/v2/practice-sessions", "POST", {"count": 2})[0], 401)

    def test_leaderboard_includes_legacy_opt_out_and_zero_answer_users(self):
        active = HttpClient(self.base)
        idle = HttpClient(self.base)
        for client, username, display_name in (
            (active, "legacy_opt_out", "历史未参加用户"),
            (idle, "zero_answer_user", "零作答用户"),
        ):
            self.assertEqual(client.request("/api/register", "POST", {
                "username": username, "displayName": display_name,
                "password": "Leaderboard-2026!", "registrationCode": "INTEGRATION-2026",
            })[0], 201)

        status, profile = active.request("/api/v2/me/profile", "PATCH", {
            "displayName": "历史未参加用户", "avatarUrl": "", "leaderboardOptIn": False,
        })
        self.assertEqual(status, 200)
        self.assertNotIn("leaderboardOptIn", profile["data"])
        question = active.request("/api/v2/questions?page=1&pageSize=1")[1]["data"]["items"][0]
        self.assertEqual(active.request("/api/attempts", "POST", {
            "questionId": question["id"], "selected": [], "skipped": False,
        })[0], 201)

        status, leaderboard = idle.request("/api/v2/leaderboard?period=all")
        self.assertEqual(status, 200)
        self.assertNotIn("optedIn", leaderboard["data"])
        rows = {item["displayName"]: item for item in leaderboard["data"]["items"]}
        self.assertIn("历史未参加用户", rows)
        self.assertIn("零作答用户", rows)
        self.assertEqual(rows["历史未参加用户"]["answered"], 1)
        self.assertEqual(rows["零作答用户"]["answered"], 0)
        self.assertEqual(rows["零作答用户"]["accuracy"], 0)

    def test_admin_crud_statuses_metadata_search_and_rbac(self):
        admin = HttpClient(self.base)
        self.assertEqual(admin.request("/api/admin/login", "POST", {
            "username": "admin", "password": "Integration-Admin-2026!",
        })[0], 200)

        status, created_admin = admin.request("/api/v2/admin/admins", "POST", {
            "username": "support_audit", "displayName": "Support Audit",
            "password": "Support-Admin-2026!", "roles": ["support_admin"],
        })
        self.assertEqual(status, 201)
        self.assertTrue(created_admin["data"]["id"])
        self.assertEqual(admin.request("/api/v2/admin/admins", "POST", {
            "username": "question_editor", "displayName": "Question Editor",
            "password": "Question-Admin-2026!", "roles": ["question_admin", "support_admin"],
        })[0], 201)

        support = HttpClient(self.base)
        self.assertEqual(support.request("/api/admin/login", "POST", {
            "username": "support_audit", "password": "Support-Admin-2026!",
        })[0], 200)
        support_dashboard = support.request("/api/v2/admin/dashboard")[1]["data"]
        self.assertEqual(support_dashboard["backups"], [])
        for protected in ("/api/v2/admin/registration-codes", "/api/v2/admin/audit-logs", "/api/v2/admin/backups"):
            self.assertEqual(support.request(protected)[0], 403, protected)

        editor = HttpClient(self.base)
        status, editor_login = editor.request("/api/admin/login", "POST", {
            "username": "question_editor", "password": "Question-Admin-2026!",
        })
        self.assertEqual(status, 200)
        self.assertEqual(set(editor_login["admin"]["roles"]), {"question_admin", "support_admin"})
        self.assertEqual(editor.request("/api/v2/admin/questions?page=1&pageSize=1")[0], 200)

        question = {
            "id": "net-admin-bug-test", "topic": "WLAN", "type": "single", "difficulty": "easy",
            "question": "Admin contract question", "options": [{"key": "a", "text": "One"}, {"key": "b", "text": "Two"}],
            "answer": ["a"], "explanation": "Initial explanation", "source": "initial", "status": "published",
        }
        self.assertEqual(support.request("/api/v2/admin/questions", "POST", question)[0], 403)
        self.assertEqual(editor.request("/api/v2/admin/questions", "POST", question)[0], 201)
        changed = {**question, "topic": "网络安全", "difficulty": "hard", "source": "updated", "status": "archived", "answer": ["B"]}
        self.assertEqual(editor.request("/api/v2/admin/questions/net-admin-bug-test", "PATCH", changed)[0], 200)
        listed = editor.request("/api/v2/admin/questions?q=net-admin-bug-test")[1]["data"]["items"][0]
        self.assertEqual((listed["topic"], listed["difficulty"], listed["source"], listed["status"], listed["revision"]), ("网络安全", "hard", "updated", "archived", 2))
        self.assertEqual(listed["answer"], ["B"])
        revisions = editor.request("/api/v2/admin/questions/net-admin-bug-test/revisions")[1]["data"]["items"]
        self.assertEqual([item["version"] for item in revisions], [2, 1])

        status, created_content = admin.request("/api/v2/admin/content", "POST", {
            "type": "announcement", "title": "Maintenance", "body": "Initial body", "source": "ops", "status": "published",
        })
        self.assertEqual(status, 201)
        content_id = created_content["data"]["id"]
        self.assertEqual(admin.request("/api/v2/admin/content", "PATCH", {
            "id": content_id, "type": "announcement", "title": "Maintenance", "body": "Updated body", "source": "ops", "status": "archived",
        })[0], 200)
        contents = admin.request("/api/v2/admin/content?type=announcement")[1]["data"]["items"]
        updated_content = next(item for item in contents if item["id"] == content_id)
        self.assertEqual((updated_content["status"], updated_content["source"]), ("archived", "ops"))

        status, paper = admin.request("/api/v2/admin/exam-papers", "POST", {
            "title": "Published from form", "questionCount": 1, "durationMinutes": 10,
            "passPercent": 60, "status": "published", "rules": {"topic": "WLAN", "types": ["single"]},
        })
        self.assertEqual(status, 201)
        self.assertEqual(paper["data"]["status"], "published")
        papers = admin.request("/api/v2/admin/exam-papers?q=Published%20from%20form")[1]["data"]["items"]
        self.assertEqual(papers[0]["status"], "published")

        audit = admin.request("/api/v2/admin/audit-logs?q=admin")[1]["data"]
        self.assertGreater(audit["total"], 0)
        self.assertEqual(admin.request("/api/v2/admin/content", "PATCH", {
            "id": "missing", "type": "announcement", "title": "x", "body": "x", "status": "draft",
        })[0], 404)
        self.assertEqual(admin.request("/api/v2/admin/exam-papers", "POST", {
            "action": "publish", "paperId": "missing", "confirm": True,
        })[0], 404)


if __name__ == "__main__":
    unittest.main()
