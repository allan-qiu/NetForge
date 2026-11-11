import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentAssetTests(unittest.TestCase):
    def test_installer_packages_all_v2_runtime_assets(self):
        script = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
        for asset in ("netforge_v2.py", "v2_http.py", "learning_features.py", "migrations/*.sql", "scripts/netforge_v2_admin.py", "dist/."):
            self.assertIn(asset, script)

    def test_installer_backs_up_preflights_and_rolls_back(self):
        script = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
        for marker in ("backup --destination", "PREFLIGHT_DB", "import-questions", "rollback_app", "app.backup", 'load_question_bank(sys.argv[2])'):
            self.assertIn(marker, script)

    def test_installer_does_not_print_secrets(self):
        script = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
        forbidden = ('echo "团队注册码', 'echo "管理员密码', 'echo "${ADMIN_PASSWORD}', 'echo "${REG_CODE}')
        for marker in forbidden:
            self.assertNotIn(marker, script)

    def test_static_v2_entrypoints_exist(self):
        for name in ("v2.html", "v2.js", "v2.css", "admin-v2.html", "admin-v2.js", "admin-v2.css", "ui-system.css", "appearance.js", "document-render.js", "privacy.html", "terms.html", "help.html", "not-found.html"):
            self.assertTrue((ROOT / "dist" / name).is_file(), name)

    def test_admin_has_stable_route_and_visible_entry(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        student_html = (ROOT / "dist" / "v2.html").read_text(encoding="utf-8")
        self.assertIn('"/admin": "admin-v2.html"', server)
        self.assertIn('"/admin/": "admin-v2.html"', server)
        self.assertIn('href="/admin"', student_html)


if __name__ == "__main__":
    unittest.main()
