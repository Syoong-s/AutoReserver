"""Graphical console API and real browser interaction tests, entirely local."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from autoreserve.browser import open_browser
from autoreserve.config import Config, Goal, Target, load_config, save_config
from autoreserve.gui import Controller, make_server
from autoreserve.state import Journal


# ==========================================
# Function: Build realistic saved booking settings without using a live account.
# Method: Use a valid SJTU route, mock labels, and a future target.
# ==========================================
def fixture_config():
    date = (datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=1)).isoformat()
    return Config(booking_url="https://sports.sjtu.edu.cn/pc/#/apointmentDetails/1/mock/健身房/0",
                  venue="模拟健身中心", goals=[Goal("ui-goal", "明日健身", [Target(date, "场地1", ["14:00-15:00"])])],
                  monitor_until_complete=True)


# ==========================================
# Function: Verify graphical state persistence and local request boundaries.
# Method: Test the actual loopback server using standard-library HTTP requests.
# ==========================================
class GuiServerFixture:
    # ==========================================
    # Function: Create an isolated local GUI server.
    # Method: Bind a random port with fresh authentication and configuration files.
    # ==========================================
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "config.json"
        save_config(fixture_config(), self.path)
        self.server = make_server(self.path, self.root / "auth", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    # ==========================================
    # Function: Release local server and test files.
    # Method: Shutdown before joining the HTTP thread.
    # ==========================================
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    # ==========================================
    # Function: Issue a token-authenticated local request.
    # Method: Serialize JSON only for POST requests.
    # ==========================================
    def request(self, path, data=None, headers=None):
        options = {"X-Reserve-Token": self.server.token} | (headers or {})
        if data is not None:
            options["Content-Type"] = "application/json"
        request = Request(self.url + path, data=json.dumps(data).encode() if data is not None else None, headers=options)
        with urlopen(request, timeout=10) as response:
            return json.load(response)


# ==========================================
# Function: Verify actual loopback API persistence and mutation boundaries.
# Method: Reuse isolated server setup across focused API assertions.
# ==========================================
class GuiApiTests(GuiServerFixture, unittest.TestCase):

    # ==========================================
    # Function: Persist multiple goals and their exact candidate priority.
    # Method: Save via the production API and read back from disk and state.
    # ==========================================
    def test_save_multi_goal_and_priority(self):
        config = fixture_config()
        target = config.goals[0].candidates[0]
        config.goals[0].candidates.insert(0, replace(target, court="场地2"))
        config.goals.append(Goal("date-two", "第二日期", [replace(target, date=(datetime.fromisoformat(target.date) + timedelta(days=1)).date().isoformat())]))
        self.request("/api/config", asdict(config))
        saved = self.request("/api/state")
        self.assertEqual(len(saved["config"]["goals"]), 2)
        self.assertEqual(saved["config"]["goals"][0]["candidates"][0]["court"], "场地2")
        self.assertTrue(load_config(self.path).monitor_until_complete)

    # ==========================================
    # Function: Reject cross-origin or unauthenticated state changes.
    # Method: Test missing tokens, malicious origins, and hidden auth paths.
    # ==========================================
    def test_local_request_boundaries(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.url + "/api/state", timeout=5)
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/action", {"action": "stop"}, {"Origin": "https://example.com"})
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.url + "/.auth/storage.json", timeout=5)
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    # ==========================================
    # Function: Preserve completed progress across GUI restarts and priority edits.
    # Method: Arm a real journal record, recreate the controller, and reorder alternatives.
    # ==========================================
    def test_completed_goals_are_locked_and_resumed(self):
        config = fixture_config()
        goal = config.goals[0]
        goal.candidates.append(replace(goal.candidates[0], court="场地2"))
        save_config(config, self.path)
        journal = Journal(self.root / "auth" / "journal.sqlite3")
        journal.arm(config.for_goal(goal), goal.candidates[0], config.goal_key(goal))
        journal.update(config.goal_key(goal), "submitted", {"order_id": "LOCAL-123"})
        journal.close()
        restarted = Controller(self.path, self.root / "auth")
        self.assertEqual(restarted.state()["goals"][0]["status"], "submitted")
        goal.candidates.reverse()
        restarted.save(asdict(config))
        self.assertEqual(restarted.state()["goals"][0]["detail"]["order_id"], "LOCAL-123")
        goal.candidates[0] = replace(goal.candidates[0], court="另一个场地")
        with self.assertRaises(ValueError):
            restarted.save(asdict(config))

    # ==========================================
    # Function: Reject parallel actions and mid-run configuration edits.
    # Method: Simulate worker ownership without opening a real login page.
    # ==========================================
    def test_busy_guard(self):
        controller = self.server.controller
        controller.busy = True
        with self.assertRaises(ValueError):
            controller.save(asdict(fixture_config()))
        with self.assertRaises(ValueError):
            controller.start("run")
        controller.busy = False

    # ==========================================
    # Function: Preserve old single-order success during visual-mode migration.
    # Method: Open legacy settings with an existing journal and verify the new goal.
    # ==========================================
    def test_legacy_progress_migration(self):
        current = fixture_config()
        config = replace(current, goals=[], targets=current.goals[0].candidates)
        save_config(config, self.path)
        journal = Journal(self.root / "auth" / "journal.sqlite3")
        journal.arm(config, config.targets[0])
        journal.update(config.task_key(), "submitted", {"order_id": "LEGACY-ORDER"})
        journal.close()
        migrated = Controller(self.path, self.root / "auth")
        state = migrated.state()
        self.assertEqual(state["goals"][0]["status"], "submitted")
        self.assertEqual(state["goals"][0]["detail"]["order_id"], "LEGACY-ORDER")


# ==========================================
# Function: Verify the dashboard's visible controls with real Chromium.
# Method: Select courts/hours, reorder priorities, and save using browser clicks.
# ==========================================
@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "启用 RUN_BROWSER_TESTS=1")
class GuiBrowserTests(GuiServerFixture, unittest.TestCase):
    # ==========================================
    # Function: Exercise the complete visual configuration flow.
    # Method: Drive only UI controls and verify the resulting saved plan.
    # ==========================================
    def test_visual_multi_select_reorder_and_save(self):
        from playwright.sync_api import expect
        with open_browser(Config(headless=True), self.root / "ui-browser") as (_, page):
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.url)
            expect(page.get_by_role("heading", name="预约工作台.")).to_be_visible()
            card = page.locator(".goal-card").first
            card.get_by_role("button", name="编辑目标").click()
            card.locator('label.choice-chip').filter(has=page.locator('[data-slot="15:00-16:00"]')).click()
            card.get_by_role("textbox", name="其他场地名称").fill("场地2")
            card.locator("[data-add-court]").click()
            card.locator("[data-add-candidates]").click()
            expect(card.locator(".candidate-row")).to_have_count(4)
            preferred = card.locator(".candidate-row").filter(has_text="场地2").filter(has_text="15:00-16:00")
            for _ in range(3):
                preferred.get_by_role("button", name="提高候选优先级").click()
            expect(card.locator(".candidate-row").first).to_contain_text("场地2")
            page.get_by_role("button", name="添加日期目标").click()
            second = page.locator(".goal-card").nth(1)
            new_date = (datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=2)).isoformat()
            second.locator("[data-date]").fill(new_date)
            second.locator("[data-date]").dispatch_event("change")
            second.locator("[data-add-candidates]").click()
            page.locator("#saveTop").click()
            expect(page.locator("#toast")).to_have_text("配置已保存")
            saved = load_config(self.path)
            self.assertEqual(len(saved.goals), 2)
            self.assertEqual(saved.goals[0].candidates[0].court, "场地2")
            self.assertEqual(saved.goals[0].candidates[0].slots, ["15:00-16:00"])
            self.assertEqual(saved.goals[1].candidates[0].date, new_date)
            self.assertEqual(errors, [])

    # ==========================================
    # Function: Keep dashboard controls usable on a narrow viewport.
    # Method: Compare scroll width and verify visible primary actions.
    # ==========================================
    def test_responsive_layout(self):
        from playwright.sync_api import expect
        with open_browser(Config(headless=True), self.root / "mobile-browser") as (_, page):
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(self.url)
            expect(page.locator(".goal-card")).to_have_count(1)
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 391)
            expect(page.get_by_role("button", name="开始预约")).to_be_visible()


if __name__ == "__main__":
    unittest.main()
