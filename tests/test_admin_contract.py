import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "dist" / "admin-v2.html").read_text(encoding="utf-8")
JS = (ROOT / "dist" / "admin-v2.js").read_text(encoding="utf-8")
CSS = (ROOT / "dist" / "admin-v2.css").read_text(encoding="utf-8")
SHARED_CSS = (ROOT / "dist" / "ui-system.css").read_text(encoding="utf-8")


class AdminV2ContractTests(unittest.TestCase):
    def test_seven_admin_modules_are_present(self):
        for module in ("dashboard", "questions", "exams", "users", "content", "permissions", "system"):
            self.assertIn(f'id="view-{module}"', HTML)
            self.assertIn(f'data-view="{module}"', HTML)

    def test_all_v2_admin_routes_are_referenced(self):
        required_routes = (
            "/api/v2/admin/dashboard",
            "/api/v2/admin/users",
            "/api/v2/admin/registration-codes",
            "/api/v2/admin/questions",
            "/api/v2/admin/question-imports/preview",
            "/api/v2/admin/exam-papers",
            "/api/v2/admin/feedback",
            "/api/v2/admin/content",
            "/api/v2/admin/audit-logs",
            "/api/v2/admin/backups",
        )
        for route in required_routes:
            self.assertIn(route, JS)

    def test_rbac_roles_and_server_side_warning_are_visible(self):
        for role in ("super_admin", "question_admin", "support_admin"):
            self.assertIn(role, JS)
        self.assertIn("前端隐藏不代替后端鉴权", HTML)
        self.assertIn("state.roles.some", JS)
        self.assertIn('state.roles.includes("super_admin")', JS)
        self.assertNotIn("event.currentTarget.reset()", JS)
        self.assertIn("const form = event.currentTarget", JS)

    def test_dangerous_operations_require_confirmation(self):
        self.assertGreaterEqual(JS.count("window.confirm("), 6)
        self.assertGreaterEqual(JS.count("confirm: true"), 6)
        for action in ("disable", "soft-delete", "revoke", "unpublish"):
            self.assertIn(action, JS)

    def test_restore_is_not_exposed_as_an_api(self):
        self.assertNotIn("/api/v2/admin/restores", JS)
        self.assertIn("恢复不在网页中直接执行", HTML)

    def test_dynamic_content_has_an_escape_boundary(self):
        self.assertIn("function escapeHtml", JS)
        self.assertRegex(JS, re.compile(r"replace\(/\[&<>'\"\]/g"))

    def test_import_is_preview_then_commit(self):
        preview = JS.index("/api/v2/admin/question-imports/preview")
        commit = JS.index("/commit", preview)
        self.assertLess(preview, commit)
        self.assertIn("失败批次不得产生半批数据", HTML)

    def test_no_fake_dashboard_numbers_in_markup(self):
        metric_block = HTML[HTML.index('id="dashboardMetrics"'):HTML.index('id="dailyTrend"')]
        self.assertNotRegex(metric_block, re.compile(r">\s*\d+[,%]?\s*<"))

    def test_drawers_render_above_the_overlay(self):
        self.assertNotIn(".app-shell{position:relative;z-index:1", CSS)
        self.assertIn(".drawer{position:fixed;z-index:52", CSS)
        self.assertIn(".overlay{position:fixed;z-index:50", CSS)

    def test_shared_design_system_theme_and_keyboard_drawers(self):
        self.assertIn('ui-system.css?v=2.4.0', HTML)
        self.assertIn('admin-v2.js?v=2.4.0', HTML)
        self.assertGreaterEqual(HTML.count('data-theme-toggle'), 2)
        self.assertIn('aria-current', JS)
        self.assertIn('drawerOpener', JS)
        self.assertIn('setAttribute("role","dialog")', JS.replace(" ", ""))
        self.assertIn('event.key==="Escape"', JS.replace(" ", ""))
        self.assertIn('.app-shell', SHARED_CSS)
        self.assertIn(':focus-visible', SHARED_CSS)


if __name__ == "__main__":
    unittest.main()
