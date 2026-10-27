"""Cross-venue priorities and shared pacing in real Chromium against local fixtures."""

from dataclasses import replace
from contextlib import contextmanager
from datetime import timedelta
import os
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

from autoreserve.catalog import ANY_COURT
from autoreserve.config import BookingContext, Goal, save_config
from autoreserve.browser import open_browser
from autoreserve.desktop_controller import DesktopController
from test_catalog import native_config
from autoreserve.runner import run_reservation
from autoreserve.sjtu import GridNotReady, SjtuPage
import test_browser as fixtures


# ==========================================
# Function: Verify cross-venue goals without accessing the real website or account.
# Method: Reuse only local browser fixtures, not the legacy test methods.
# ==========================================
@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "设置 RUN_BROWSER_TESTS=1 启用多场馆测试")
class MultiVenueTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.BrowserTests.tearDownClass.__func__)
    setUp = fixtures.BrowserTests.setUp
    tearDown = fixtures.BrowserTests.tearDown
    browser = fixtures.BrowserTests.browser

    # ==========================================
    # Function: Build an exact scoped target for one local venue/project.
    # Method: Encode labels in the fixture URL so DOM verification distinguishes venues.
    # ==========================================
    def target_at(self, name, scenario="success", sport="健身房", **changes):
        context = BookingContext(self.base + "/?" + urlencode({"venue": name, "sport": sport, "scenario": scenario}), name, sport)
        return replace(self.target, context=context, **changes)

    # ==========================================
    # Function: Monitor alternative venues even when the first has no vacancy.
    # Method: Require one order from the second venue after reading both scoped snapshots.
    # ==========================================
    def test_unavailable_first_venue_falls_back_to_second(self):
        goal = Goal("fallback", "健身", [self.target_at("A馆", "soldout"), self.target_at("B馆")])
        with self.browser(goals=[goal], targets=[], monitor_until_complete=True) as (config, _, page):
            events, submits = [], []
            page.on("request", lambda r: submits.append(page.url) if r.url.endswith("/ConfirmOrder") else None)
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True, events.append)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["goals"][0]["detail"]["venue"], "B馆")
            self.assertEqual(len(submits), 1)
            self.assertTrue(any("2 个日期/场馆组合" in e["message"] for e in events))

    # ==========================================
    # Function: Respect global candidate order when both venues have vacancies.
    # Method: Read A and B, then return to A and produce only its order.
    # ==========================================
    def test_both_venues_available_uses_highest_priority(self):
        goal = Goal("priority", "择优", [self.target_at("A馆"), self.target_at("B馆", sport="羽毛球")])
        with self.browser(goals=[goal], targets=[]) as (config, _, page):
            submits = []
            page.on("request", lambda r: submits.append(r.url) if r.url.endswith("/ConfirmOrder") else None)
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["goals"][0]["detail"]["venue"], "A馆")
            self.assertEqual(result["goals"][0]["detail"]["sport"], "健身房")
            self.assertEqual(len(submits), 1)
            self.assertEqual(page.evaluate("payClicks"), 0)

    # ==========================================
    # Function: Keep other goals monitored when one venue remains sold out.
    # Method: Require the available second goal to complete while the first remains pending.
    # ==========================================
    def test_other_goal_is_not_blocked_by_unavailable_venue(self):
        goals = [Goal("wait", "等待", [self.target_at("A馆", "soldout")]), Goal("ready", "可用", [self.target_at("B馆")])]
        with self.browser(goals=goals, targets=[]) as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True)
            self.assertEqual(result["status"], "pending")
            self.assertEqual(result["completed"], 1)
            self.assertTrue(all(g["last_checked_at"] for g in result["goals"]))

    # ==========================================
    # Function: Avoid selecting an already claimed slot for the next cross-venue goal.
    # Method: Complete A, then require another goal's fallback to B instead of duplicating A.
    # ==========================================
    def test_two_goals_do_not_duplicate_a_claimed_slot(self):
        a, b = self.target_at("A馆"), self.target_at("B馆")
        goals = [Goal("first", "第一个", [a, b]), Goal("second", "第二个", [a, b])]
        with self.browser(goals=goals, targets=[], monitor_until_complete=True) as (config, _, page):
            events, submits = [], []
            page.on("request", lambda r: submits.append(page.url) if r.url.endswith("/ConfirmOrder") else None)
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True, events.append)
            self.assertEqual(result["status"], "completed")
            self.assertEqual([g["detail"]["venue"] for g in result["goals"]], ["A馆", "B馆"])
            self.assertEqual(len(submits), 2)
            self.assertEqual(sum(bool(e.get("completed_goal")) for e in events), 2)

    # ==========================================
    # Function: Share one context/date read for many pending goals.
    # Method: Run two unavailable rounds and require only one tab refresh after the seed.
    # ==========================================
    def test_same_context_goals_share_snapshot_and_budget(self):
        goals = [Goal(f"shared-{i}", str(i), [self.target_at("A馆", "soldout")]) for i in range(5)]
        with self.browser(goals=goals, targets=[], monitor_until_complete=True) as (config, _, page):
            events = []
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True, events.append,
                                     stop=lambda: sum(e["message"].startswith("等待空位，") for e in events) >= 2)
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(result["monitor"]["reads"], 2)
            self.assertEqual(page.evaluate("inventoryRequests"), 1)

    # ==========================================
    # Function: Keep an unlimited multi-venue monitor running until cooperative stop.
    # Method: Require two unavailable rounds with a zero duration and monitor-until-complete disabled.
    # ==========================================
    def test_zero_duration_continues_until_stopped(self):
        goal=Goal('unlimited','持续监测',[self.target_at('A馆','soldout')])
        with self.browser(goals=[goal],targets=[],max_poll_seconds=0.0,monitor_until_complete=False) as (config,_,page):
            events=[]
            result=run_reservation(page,config,self.journal,self.root/'artifacts',True,events.append,
                                   stop=lambda:sum(e['message'].startswith('等待空位，') for e in events)>=2)
            self.assertEqual(result['status'],'stopped')
            self.assertGreaterEqual(result['monitor']['reads'],2)
            self.assertEqual(page.evaluate('submitClicks'),0)

    # ==========================================
    # Function: Recover the user's stop/restart symptom by replacing one real stale browser session.
    # Method: Stall the first page's grid reads, then require one local order from a fresh browser and no duplicate submit.
    # ==========================================
    def test_stalled_monitor_reopens_browser_once(self):
        path=self.root/'desktop-config.json'
        save_config(native_config(),path)
        controller=DesktopController(path,self.root/'recovery-auth')
        goal=Goal('recover-browser','恢复监测',[self.target_at('A馆')])
        config=replace(self.config,goals=[goal],targets=[],monitor_until_complete=True,max_poll_seconds=0)
        pages,submits=[],[]
        original=SjtuPage.loaded_snapshot

        # ==========================================
        # Function: Track each actual browser page and its order requests across replacement.
        # Method: Delegate unchanged to the production browser manager using only loopback fixtures.
        # ==========================================
        @contextmanager
        def browser(*args,**kwargs):
            with open_browser(*args,**kwargs) as (context,page):
                pages.append(page)
                page.on('request',lambda request:submits.append(request.url) if request.url.endswith('/ConfirmOrder') else None)
                yield context,page

        # ==========================================
        # Function: Reproduce a JavaScript/renderer state that only disappears when the browser is replaced.
        # Method: Fail only the first page, leaving the replacement's real DOM and native request handling unchanged.
        # ==========================================
        def snapshot(site,*args,**kwargs):
            if pages and site.page is pages[0]:
                raise GridNotReady('模拟旧页面加载遮罩卡住')
            return original(site,*args,**kwargs)

        with patch('autoreserve.desktop_controller.open_browser',browser), \
                patch('autoreserve.desktop_controller.HOME_URL',self.base), \
                patch('autoreserve.desktop_controller.DirectoryClient.authenticated',return_value=True), \
                patch('autoreserve.runner.ReadPacer',fixtures.FastPacer), \
                patch.object(SjtuPage,'loaded_snapshot',snapshot):
            controller.monitor_with_recovery(config,self.root/'recovery-artifacts')
        self.assertEqual(controller.result['status'],'completed')
        self.assertEqual(len(pages),2)
        self.assertTrue(all(page.is_closed() for page in pages))
        self.assertEqual(len(submits),1)
        self.assertTrue((self.root/'recovery-auth/session.json').exists())

    # ==========================================
    # Function: Pause all venues when an availability request is rate limited.
    # Method: Return one 429 with Retry-After and require recovery before any order.
    # ==========================================
    def test_429_cooldown_is_shared_across_venues(self):
        day = (self.today + timedelta(days=1)).isoformat()
        goal = Goal("rate", "限流", [self.target_at("A馆", date=day), self.target_at("B馆", date=day)])
        with self.browser(goals=[goal], targets=[], monitor_until_complete=True) as (config, _, page):
            calls, events = [], []

            # ==========================================
            # Function: Throttle only the first local inventory request.
            # Method: Keep the production response observer and shared pacer active.
            # ==========================================
            def inventory(route):
                calls.append(route.request.url)
                route.fulfill(status=429 if len(calls) == 1 else 200,
                              headers={"retry-after": "120", "content-type": "application/json"}, body='{"code":0,"data":[]}')

            page.route("**/manage/fieldDetail/queryFieldSituation", inventory)
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True, events.append)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(any(e["monitor"]["cooling_down"] and "120" in e["monitor"]["reason"] for e in events))
            self.assertGreaterEqual(len(calls), 3)

    # ==========================================
    # Function: Refuse to order from partial snapshots after a different venue denies access.
    # Method: Deny the second booking document and require zero submissions across both venues.
    # ==========================================
    def test_403_at_one_venue_pauses_all_orders(self):
        goal = Goal("denied", "拒绝访问", [self.target_at("A馆"), self.target_at("B馆")])
        with self.browser(goals=[goal], targets=[]) as (config, _, page):
            page.route("**/*venue=B*", lambda route: route.fulfill(status=403, body="Forbidden"))
            submits = []
            page.on("request", lambda r: submits.append(r.url) if r.url.endswith("/ConfirmOrder") else None)
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True)
            self.assertEqual(result["status"], "access_limited")
            self.assertEqual(submits, [])

    # ==========================================
    # Function: Stop an uncertain submission without falling back to another venue.
    # Method: Return success without an order ID and require one persisted unknown result.
    # ==========================================
    def test_unknown_order_does_not_try_another_venue(self):
        goal = Goal("unknown", "需核对", [self.target_at("A馆", "missing-id"), self.target_at("B馆")])
        with self.browser(goals=[goal], targets=[], monitor_until_complete=True) as (config, _, page):
            events, submits = [], []
            page.on("request", lambda r: submits.append(r.url) if r.url.endswith("/ConfirmOrder") else None)
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True, events.append)
            self.assertEqual(result["status"], "needs_attention")
            self.assertEqual(len(submits), 1)
            self.assertFalse(any(e.get("completed_goal") for e in events))

    # ==========================================
    # Function: Recover from an unfinished pre-submit grid without duplicate orders.
    # Method: Fail verification once, then complete under the same durable goal identity.
    # ==========================================
    def test_pre_submit_loading_retry_is_safe(self):
        goal = Goal("loading", "加载恢复", [self.target_at("A馆", court=ANY_COURT)])
        original, calls = SjtuPage.prepare_submit, []

        # ==========================================
        # Function: Simulate one unfinished final DOM verification.
        # Method: Fail before the submission latch is armed, then delegate normally.
        # ==========================================
        def delayed(site, target):
            calls.append(True)
            if len(calls) == 1:
                raise GridNotReady("预约网格未加载完成")
            return original(site, target)

        with self.browser(goals=[goal], targets=[], monitor_until_complete=True) as (config, _, page):
            submits = []
            page.on("request", lambda r: submits.append(r.url) if r.url.endswith("/ConfirmOrder") else None)
            with patch.object(SjtuPage, "prepare_submit", delayed):
                result = run_reservation(page, config, self.journal, self.root / "artifacts", True)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(submits), 1)
            self.assertEqual(len(calls), 2)

    # ==========================================
    # Function: Verify real pacing logic covers every venue switch, not only date refreshes.
    # Method: Advance a virtual clock while pumping real browser events between budgeted reads.
    # ==========================================
    def test_global_minimum_spacing_includes_venue_switches(self):
        from autoreserve.monitoring import ReadPacer
        from test_monitoring import FakeClock
        clock, stamps = FakeClock(), []
        goal = Goal("paced", "全局间隔", [self.target_at("A馆"), self.target_at("B馆")])

        # ==========================================
        # Function: Exercise the actual shared deadline algorithm without waiting 30 real seconds.
        # Method: Pump Chromium events while advancing only the scheduler's virtual time.
        # ==========================================
        class VirtualPacer(ReadPacer):
            # ==========================================
            # Function: Reserve one real monitor operation under the production wait loop.
            # Method: Substitute only the browser wait clock and record permitted start times.
            # ==========================================
            def wait(self, page, stop, progress=None):
                # ==========================================
                # Function: Adapt only test sleeping while retaining real page event handling.
                # Method: Advance the same virtual clock used by the production limiter.
                # ==========================================
                class Pump:
                    # ==========================================
                    # Function: Advance test time while keeping asynchronous browser I/O live.
                    # Method: Replace a long sleep with a short event pump.
                    # ==========================================
                    def wait_for_timeout(self, milliseconds):
                        clock.wait_for_timeout(milliseconds)
                        page.wait_for_timeout(1)
                result = super().wait(Pump(), stop, progress)
                if result:
                    stamps.append(clock.now)
                return result

        with self.browser(goals=[goal], targets=[]) as (config, _, page):
            pacer = VirtualPacer(config, clock=clock.monotonic, wall=clock.wall, jitter=lambda a, b: 0)
            with patch("autoreserve.runner.ReadPacer", return_value=pacer):
                result = run_reservation(page, config, self.journal, self.root / "artifacts", True)
            self.assertEqual(result["status"], "completed")
            self.assertGreaterEqual(len(stamps), 3)
            self.assertTrue(all(b-a >= config.poll_interval_seconds for a, b in zip(stamps, stamps[1:])))

    # ==========================================
    # Function: Keep waiting when a fallback venue's date is unopened rather than blocked.
    # Method: Claim the first venue's slot and hide the second venue's date tab.
    # ==========================================
    def test_unopened_fallback_is_pending_not_conflict(self):
        day = (self.today+timedelta(days=1)).isoformat()
        a, b = self.target_at("A馆", date=day), self.target_at("B馆", date=day)
        goal, original = Goal("unopened", "等待开放", [a, b]), SjtuPage.choose_date

        # ==========================================
        # Function: Reproduce different booking horizons for two venues.
        # Method: Report the second date as unopened without issuing an inventory read.
        # ==========================================
        def unavailable_date(site, value):
            return False if site.config.venue == "B馆" else original(site, value)

        with self.browser(goals=[goal], targets=[]) as (config, _, page):
            self.journal.arm(config.for_target(a), a, "other-goal")
            self.journal.update("other-goal", "submitted", {"order_id": "EXISTING"})
            with patch.object(SjtuPage, "choose_date", unavailable_date):
                result = run_reservation(page, config, self.journal, self.root / "artifacts", True)
            self.assertEqual(result["status"], "pending")
            self.assertEqual(result["goals"][0]["status"], "pending")

    # ==========================================
    # Function: Resume a completed multi-venue goal without checking or ordering again.
    # Method: Finish once, reorder candidates, and require no requests on the second run.
    # ==========================================
    def test_completed_multi_venue_goal_survives_reordering(self):
        goal = Goal("resume", "恢复", [self.target_at("A馆"), self.target_at("B馆")])
        with self.browser(goals=[goal], targets=[]) as (config, _, page):
            run_reservation(page, config, self.journal, self.root / "first", True)
            goal.candidates.reverse()
            requests = []
            page.on("request", lambda request: requests.append(request.url))
            result = run_reservation(page, config, self.journal, self.root / "second", True)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(requests, [])

    # ==========================================
    # Function: Wait for a daily trigger without reading venue inventory.
    # Method: Run a rule-only workspace before its trigger and stop after its idle status event.
    # ==========================================
    def test_daily_wait_has_no_inventory_reads(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from autoreserve.config import RepeatRule
        now = datetime.combine(self.today, datetime.min.time(), ZoneInfo("Asia/Shanghai")).replace(hour=12)
        rule = RepeatRule("future", "定时", [self.target_at("B馆")], 1, "12:01")
        events = []
        with self.browser(goals=[], targets=[], repeat_rules=[rule]) as (config, _, page):
            requests = []
            page.on("request", lambda request: requests.append(request.url))
            with patch("autoreserve.repeat.datetime") as clock, patch("autoreserve.multivenue.datetime") as monitor_clock:
                clock.now.return_value = monitor_clock.now.return_value = now
                result = run_reservation(page, config, self.journal, self.root / "artifacts", True, events.append,
                                         stop=lambda: any("下次生成" in e["message"] for e in events))
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(requests, [])
            self.assertEqual(self.journal.entries(), [])

    # ==========================================
    # Function: Keep a weekly-only workspace idle on an unselected execution day.
    # Method: Run the real browser adapter with a fixed excluded weekday and prohibit all requests.
    # ==========================================
    def test_weekly_disabled_day_waits_without_inventory_reads(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from autoreserve.config import RepeatRule
        now = datetime.combine(self.today, datetime.min.time(), ZoneInfo("Asia/Shanghai")).replace(hour=13)
        rule = RepeatRule("weekly", "每周预约", [self.target_at("B馆")], 1, "12:00",
                          weekdays=[(now.weekday()+2) % 7])
        events = []
        with self.browser(goals=[], targets=[], repeat_rules=[rule]) as (config, _, page):
            requests = []
            page.on("request", lambda request: requests.append(request.url))
            with patch("autoreserve.repeat.datetime") as clock, patch("autoreserve.multivenue.datetime") as monitor_clock:
                clock.now.return_value = monitor_clock.now.return_value = now
                result = run_reservation(page, config, self.journal, self.root / "weekly", True, events.append,
                                         stop=lambda: any("下次生成" in e["message"] for e in events))
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(requests, [])
            self.assertEqual(self.journal.entries(), [])
            expected = (now + timedelta(days=2)).strftime("%m-%d")
            self.assertTrue(any(expected in e["message"] for e in events if "下次生成" in e["message"]))

    # ==========================================
    # Function: Continue resident daily monitoring after a successful order and create a new day.
    # Method: Advance a controlled local clock on completion and require two separate date orders.
    # ==========================================
    def test_daily_rollover_submits_new_date_once(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from autoreserve.config import RepeatRule
        now = datetime.combine(self.today, datetime.min.time(), ZoneInfo("Asia/Shanghai")).replace(hour=12)
        rule = RepeatRule("rolling", "每天", [self.target_at("B馆")], 0, "12:00")
        events, completed, persisted = [], [], []
        with self.browser(goals=[], targets=[], repeat_rules=[rule]) as (config, _, page):
            with patch("autoreserve.repeat.datetime") as clock, patch("autoreserve.multivenue.datetime") as monitor_clock:
                clock.now.return_value = monitor_clock.now.return_value = now

                # ==========================================
                # Function: Advance the day only after a confirmed mock order.
                # Method: Retain all progress events and stop after the second unique occurrence.
                # ==========================================
                def progress(event):
                    events.append(event)
                    if event.get("completed_goal"):
                        completed.append(event["completed_goal"])
                        clock.now.return_value = monitor_clock.now.return_value = now + timedelta(days=1)

                result = run_reservation(page, config, self.journal, self.root / "artifacts", True, progress,
                                         stop=lambda: len(completed) >= 2,
                                         refresh=lambda c: persisted.append([g.id for g in c.goals]))
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(len(completed), 2)
            self.assertNotEqual(completed[0]["id"], completed[1]["id"])
            self.assertEqual(len(self.journal.entries()), 2)
            self.assertEqual(len(persisted), 2)
            self.assertEqual(page.evaluate("payClicks"), 0)

    # ==========================================
    # Function: Add a daily occurrence while an older unavailable goal is still monitored.
    # Method: Make a second venue due between rounds and verify both dates retain independent progress.
    # ==========================================
    def test_daily_new_goal_joins_unfinished_monitor(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from autoreserve.config import RepeatRule
        now = datetime.combine(self.today, datetime.min.time(), ZoneInfo("Asia/Shanghai")).replace(hour=12)
        old = Goal("older", "等待空位", [self.target_at("A馆", "soldout")])
        rule = RepeatRule("joining", "新目标", [self.target_at("B馆")], 0, "12:01")
        completed = []
        with self.browser(goals=[old], targets=[], repeat_rules=[rule]) as (config, _, page):
            with patch("autoreserve.repeat.datetime") as clock, patch("autoreserve.multivenue.datetime") as monitor_clock:
                clock.now.return_value = monitor_clock.now.return_value = now

                # ==========================================
                # Function: Release the new daily target after an unavailable monitoring round.
                # Method: Advance only local scheduling time, preserving the real browser adapter.
                # ==========================================
                def progress(event):
                    if event["message"].startswith("等待空位，"):
                        clock.now.return_value = monitor_clock.now.return_value = now + timedelta(minutes=1)
                    if event.get("completed_goal"):
                        completed.append(event["completed_goal"])

                result = run_reservation(page, config, self.journal, self.root / "artifacts", True, progress,
                                         stop=lambda: bool(completed))
            self.assertEqual(result["status"], "stopped")
            original = next(g for g in result["goals"] if g["id"] == "older")
            self.assertEqual(original["status"], "pending")
            self.assertGreaterEqual(original["check_count"], 2)
            self.assertEqual(completed[0]["detail"]["venue"], "B馆")
            self.assertEqual(len(self.journal.entries()), 1)
