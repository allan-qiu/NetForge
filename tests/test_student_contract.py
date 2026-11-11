import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "dist" / "v2.html").read_text(encoding="utf-8")
JS = (ROOT / "dist" / "v2.js").read_text(encoding="utf-8")
CSS = (ROOT / "dist" / "v2.css").read_text(encoding="utf-8")
SHARED_CSS = (ROOT / "dist" / "ui-system.css").read_text(encoding="utf-8")


class StudentV2ContractTests(unittest.TestCase):
    def test_home_practice_starts_a_session_instead_of_opening_the_bank(self):
        self.assertIn('startPractice({count:20},$("homePracticeBtn"))', JS)
        self.assertNotIn('$("homePracticeBtn").addEventListener("click",()=>navigate("bank"))', JS)
        self.assertIn('if (trigger?.disabled) return', JS)

    def test_completed_practice_returns_home_only_after_server_accepts_completion(self):
        completed_patch = 'body: { status: "completed", currentIndex: session.index }'
        self.assertIn(completed_patch, JS)
        self.assertLess(JS.index(completed_patch), JS.index('state.practice = null; toast("本轮练习已完成")'))
        self.assertIn('toast(`练习进度未保存：${error.message}`)', JS)
        self.assertIn('movePractice(Number(button.dataset.index) - state.practice.index)', JS)

    def test_leaderboard_opt_in_control_and_client_logic_are_removed(self):
        self.assertNotIn("leaderboardOptIn", HTML)
        self.assertNotIn("leaderboardOptIn", JS)
        self.assertNotIn("rankingOptIn", HTML)
        self.assertNotIn("rankingOptIn", JS)

    def test_feedback_cancel_is_not_a_submit_action(self):
        self.assertIn('id="cancelFeedback" class="secondary" type="button"', HTML)
        self.assertIn('$("cancelFeedback").addEventListener("click",()=>$("feedbackDialog").close())', JS)

    def test_exam_spacing_ranking_profile_changelog_and_avatar_controls(self):
        self.assertIn('class="form-grid exam-config"', HTML)
        self.assertIn(".exam-config .primary{grid-column:1/-1;margin-top:16px}", CSS)
        self.assertIn('id="rankingProfileDialog"', HTML)
        self.assertIn("/api/v2/users/", JS)
        self.assertIn("item.body", JS)
        self.assertIn("pageSize=200", JS)
        self.assertIn('id="avatarFile"', HTML)
        self.assertNotIn('name="avatarUrl"', HTML)
        self.assertIn("/api/v2/me/avatar", JS)

    def test_shared_design_system_and_accessible_states(self):
        self.assertIn('ui-system.css?v=2.4.0', HTML)
        self.assertIn('v2.js?v=2.4.0', HTML)
        self.assertIn('aria-pressed="false"', HTML)
        self.assertIn('aria-current', JS)
        self.assertIn('min-height:44px', SHARED_CSS.replace(" ", ""))
        compact_css = SHARED_CSS.replace(" ", "")
        self.assertIn('@media(max-width:680px)', compact_css)
        self.assertIn('@media(prefers-reduced-motion:reduce)', compact_css)
        self.assertIn(':focus-visible', SHARED_CSS)


if __name__ == "__main__":
    unittest.main()
