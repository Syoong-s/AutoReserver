"""Real Chromium end-to-end tests against a local-only fixture server."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from zoneinfo import ZoneInfo

from autoreserve.browser import open_browser, origin_of, save_session
from autoreserve.config import BookingContext, Config, Goal, Target
from autoreserve.runner import run_reservation
from autoreserve.sjtu import GridNotReady, SjtuPage, candidate_cells
from autoreserve.catalog import ANY_COURT
from autoreserve.monitoring import ReadPacer
from unittest.mock import patch
from autoreserve.state import Journal
from demo.server import Handler


# ==========================================
# Function: Keep mock integration tests fast while testing real pacing separately.
# Method: Skip wall-clock waits only in the test harness, retaining throttle detection.
# ==========================================
class FastPacer(ReadPacer):
    # ==========================================
    # Function: Keep full-round integration fixtures independent from production wall-clock cadence.
    # Method: Enter a round immediately while dedicated clock tests verify real scheduling and backoff.
    # ==========================================
    def begin_cycle(self, page, stop, progress=None, wake=None):
        self._in_cycle=True
        self._cycle_start=self.clock()
        return not stop()
    # ==========================================
    # Function: Reserve a mock read without waiting for production intervals.
    # Method: Preserve access denial checks and response feedback in integration tests.
    # ==========================================
    def wait(self, page, stop, progress=None):
        self.check_denial()
        if progress:
            progress(self.state())
        if stop():
            return False
        self.next_allowed = self.clock()
        self.cooldown_until = self.clock()
        self.reads += 1
        return True


# ==========================================
# Function: Verify actual browser behavior without contacting the target website.
# Method: Run the production adapter against equivalent DOM and response contracts.
# ==========================================
@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "设置 RUN_BROWSER_TESTS=1 启用真实浏览器测试")
class BrowserTests(unittest.TestCase):
    # ==========================================
    # Function: Keep the optimized selection path from trusting a grid while a native recheck is in flight.
    # Method: Start an unmasked delayed inventory chain that clears selection and require failure before order submission.
    # ==========================================
    def test_selection_fast_path_waits_for_native_recheck(self):
        with self.browser(max_total_price='50.00') as (config,_,page):
            page.goto(config.booking_url)
            site=SjtuPage(page,config)
            try:
                site.ready()
                site.choose_sport()
                site.choose_date(self.target.date)
                self.assertTrue(site.select(self.target))
                page.evaluate("""() => {
                    void fetch('/manage/fieldDetail/queryFieldReserveSituationIsFull', {
                      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({delay:0.35})
                    }).then(() => { selected=[]; render(); });
                }""")
                with self.assertRaisesRegex(RuntimeError,'已选数量'):
                    site.prepare_submit(self.target)
                self.assertEqual(page.evaluate('submitClicks'),0)
                self.assertEqual(self.journal.entries(),[])
            finally:
                site.close()

    # ==========================================
    # Function: Submit an available target without repeating DOM quiet-window sleeps on the selection hot path.
    # Method: Use a production-style scoped venue and observe explicit adapter waits from availability to the final request.
    # ==========================================
    def test_order_hot_path_avoids_repeated_settling(self):
        goal=Goal('fast-order','快速下单',[self.target])
        found,submits,waits=[],[],[]

        # ==========================================
        # Function: Mark the beginning of the discovered-candidate hot path.
        # Method: Use the production progress event immediately before selection and notice acceptance.
        # ==========================================
        def progress(event):
            if '发现 1 个可预约目标' in event['message']:
                found.append(time.monotonic())

        with self.browser(goals=[goal],targets=[]) as (config,_,page):
            config.goals=[replace(goal,candidates=[replace(self.target,context=BookingContext(config.booking_url,config.venue,config.sport))])]
            original=page.wait_for_timeout

            # ==========================================
            # Function: Detect redundant settle sleeps after the target has been found.
            # Method: Preserve all browser pumping while recording waits before the final request.
            # ==========================================
            def wait(milliseconds):
                if found and not submits:
                    waits.append(milliseconds)
                return original(milliseconds)

            page.on('request',lambda request:submits.append(time.monotonic()) if request.url.endswith('/ConfirmOrder') else None)
            with patch.object(page,'wait_for_timeout',wait):
                result=run_reservation(page,config,self.journal,self.root/'artifacts',True,progress)
            self.assertEqual(result['status'],'completed')
            self.assertEqual(len(submits),1)
            self.assertEqual(waits,[])
            self.assertEqual(page.evaluate('payClicks'),0)
            print(f'Local candidate-to-submit request: {(submits[0]-found[0])*1000:.1f} ms')

    # ==========================================
    # Function: Start a local server on an automatically assigned port.
    # Method: Bind only to loopback and serve in a background thread.
    # ==========================================
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    # ==========================================
    # Function: Shut down the fixture server cleanly.
    # Method: Stop the server before joining its thread.
    # ==========================================
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    # ==========================================
    # Function: Isolate authentication, journals, and artifacts for each test.
    # Method: Use a temporary directory and dates aligned with the browser timezone.
    # ==========================================
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        self.target = Target(self.today.isoformat(), "场地1", ["14:00-15:00"])
        self.config = Config(booking_url=self.base, venue="模拟健身中心", sport="健身房",
                             targets=[self.target], headless=True, max_poll_seconds=1,
                             action_timeout_ms=3000, result_timeout_seconds=1,
                             grid_timeout_seconds=3,
                             verification_timeout_seconds=3)
        self.journal = Journal(self.root / "auth" / "journal.sqlite3")

    # ==========================================
    # Function: Remove temporary artifacts and release database handles.
    # Method: Close SQLite before removing the temporary tree.
    # ==========================================
    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    # ==========================================
    # Function: Open a production browser against a chosen fixture scenario.
    # Method: Apply local-only URL validation explicitly in the test harness.
    # ==========================================
    @contextmanager
    def browser(self, scenario="success", **updates):
        config = replace(self.config, booking_url=f"{self.base}/?scenario={scenario}", **updates)
        config.validate(require_targets=True, allow_local=True)
        with patch("autoreserve.runner.ReadPacer", FastPacer), open_browser(config, self.root / "auth") as (context, page):
            yield config, context, page

    # ==========================================
    # Function: Verify the exact user flow ends after submission.
    # Method: Assert one submit, zero pay clicks, and a persisted order ID.
    # ==========================================
    def test_submit_stops_before_payment(self):
        with self.browser() as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(result["status"], "submitted")
            self.assertEqual(result["order_id"], "LOCAL-DEMO-ORDER")
            self.assertEqual(page.evaluate("submitClicks"), 1)
            self.assertEqual(page.evaluate("payClicks"), 0)
            self.assertEqual(self.journal.entries()[0]["status"], "submitted")
            with self.assertRaises(RuntimeError):
                self.journal.ensure_new(config)

    # ==========================================
    # Function: Verify preview mode cannot create an order.
    # Method: Assert the terms dialog stays closed and the journal remains empty.
    # ==========================================
    def test_preview_has_no_submit(self):
        with self.browser() as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts")
            self.assertEqual(result["status"], "preview")
            self.assertEqual(page.evaluate("submitClicks"), 0)
            self.assertFalse(page.locator(".el-dialog").is_visible())
            self.assertEqual(self.journal.entries(), [])

    # ==========================================
    # Function: Make duplicate-slot conflicts actionable and permit a fresh goal after explicit local-history removal.
    # Method: Submit once on the loopback fixture, verify a blocked second goal and then successfully retry after deleting its guard.
    # ==========================================
    def test_history_delete_allows_new_goal_for_identical_slot(self):
        first=Goal('history-first','原目标',[self.target])
        retry=Goal('history-retry','重新预约',[self.target])
        with self.browser(goals=[first],targets=[]) as (config,_,page):
            result=run_reservation(page,config,self.journal,self.root/'artifacts',True)
            self.assertEqual(result['status'],'completed')
            key=config.goal_key(first)
        with self.browser(goals=[retry],targets=[]) as (config,_,page):
            result=run_reservation(page,config,self.journal,self.root/'artifacts',True)
            self.assertEqual(result['status'],'needs_attention')
            self.assertIn('历史订单',result['message'])
            self.assertEqual(page.evaluate('submitClicks'),0)
        self.journal.delete_history('task:'+key)
        with self.browser(goals=[retry],targets=[]) as (config,_,page):
            result=run_reservation(page,config,self.journal,self.root/'artifacts',True)
            self.assertEqual(result['status'],'completed')
            self.assertEqual(page.evaluate('submitClicks'),1)
            self.assertEqual(page.evaluate('payClicks'),0)

    # ==========================================
    # Function: Verify multi-hour bookings are selected as a group.
    # Method: Require both consecutive cells before the single final submission.
    # ==========================================
    def test_consecutive_hours(self):
        target = replace(self.target, slots=["14:00-15:00", "15:00-16:00"])
        with self.browser(targets=[target]) as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(result["status"], "submitted")
            self.assertEqual(page.locator(".selected-seat").count(), 2)

    # ==========================================
    # Function: Avoid silently accepting a site's one-slot limit.
    # Method: Simulate deselection of the first hour and require a hard stop.
    # ==========================================
    def test_site_selection_limit(self):
        target = replace(self.target, slots=["14:00-15:00", "15:00-16:00"])
        with self.browser("limit", targets=[target]) as (config, _, page):
            with self.assertRaises(RuntimeError):
                run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(page.evaluate("submitClicks"), 0)

    # ==========================================
    # Function: Skip sold-out times without clicking them.
    # Method: Run a bounded polling cycle against an entirely gray grid.
    # ==========================================
    def test_sold_out(self):
        with self.browser("soldout") as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(result["status"], "no_availability")
            self.assertEqual(page.evaluate("submitClicks"), 0)

    # ==========================================
    # Function: Respect candidate priority and asynchronous date switching.
    # Method: Skip a sold-out hour and choose court two on tomorrow's tab.
    # ==========================================
    def test_fallback_date_and_court(self):
        unavailable = replace(self.target, slots=["13:00-14:00"])
        fallback = replace(self.target, date=(self.today + timedelta(days=1)).isoformat(), court="场地2")
        with self.browser(targets=[unavailable, fallback], max_poll_seconds=5) as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(result["date"], fallback.date)
            self.assertEqual(result["court"], "场地2")
            self.assertEqual(page.evaluate("submitClicks"), 1)

    # ==========================================
    # Function: Prevent over-budget orders in a real DOM flow.
    # Method: Change the visible price and require rejection before terms.
    # ==========================================
    def test_price_limit(self):
        with self.browser("success&price=15",max_total_price='10.0') as (config, _, page):
            with self.assertRaises(RuntimeError):
                run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(page.evaluate("submitClicks"), 0)

    # ==========================================
    # Function: Run a zero-duration monitor and accept a priced selection when its monetary cap is zero.
    # Method: Exercise the real legacy adapter in preview mode and require no order or payment.
    # ==========================================
    def test_zero_duration_and_price_allow_unlimited_preview(self):
        with self.browser('success&price=15',max_poll_seconds=0.0,max_total_price='0.0') as (config,_,page):
            result=run_reservation(page,config,self.journal,self.root/'artifacts',submit=False)
            self.assertEqual(result['status'],'preview')
            self.assertEqual(page.evaluate('submitClicks'),0)
            self.assertEqual(page.evaluate('payClicks'),0)

    # ==========================================
    # Function: Preserve a definite rejection without an automatic second order.
    # Method: Observe the mock service's rejection response and count clicks.
    # ==========================================
    def test_rejection_is_not_retried(self):
        with self.browser("rejected") as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(page.evaluate("submitClicks"), 1)

    # ==========================================
    # Function: Avoid false success when the response has no order ID.
    # Method: Require an unknown result and keep the durable submission latch.
    # ==========================================
    def test_missing_order_id_is_unknown(self):
        with self.browser("missing-id") as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(self.journal.entries()[0]["status"], "unknown")

    # ==========================================
    # Function: Handle a connection loss after the final click conservatively.
    # Method: Abort only the local confirmation request and wait for timeout.
    # ==========================================
    def test_disconnection_is_not_retried(self):
        with self.browser() as (config, _, page):
            page.route("**/venue/personal/ConfirmOrder", lambda route: route.abort())
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(page.evaluate("submitClicks"), 1)

    # ==========================================
    # Function: Stop gracefully when verification requires a visible browser.
    # Method: Observe challenge code 1002 in headless mode without solving it.
    # ==========================================
    def test_challenge_needs_user(self):
        with self.browser("captcha") as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(result["status"], "needs_verification")
            self.assertEqual(page.evaluate("submitClicks"), 1)

    # ==========================================
    # Function: Resume result observation after simulated human verification.
    # Method: Trigger only the mock verification button after its appearance.
    # ==========================================
    def test_manual_verification_callback(self):
        with self.browser("captcha") as (config, _, page):
            # The browser stays headless; only the observer's policy is changed for this mock test.
            config.headless = False
            page.add_init_script("""setInterval(() => {
                const e = document.querySelector('.verifybox');
                if (e && e.getClientRects().length) document.querySelector('#verify').click();
            }, 250);""")
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(result["status"], "submitted")
            self.assertEqual(page.evaluate("payClicks"), 0)

    # ==========================================
    # Function: Restore sessionStorage as well as localStorage and cookies.
    # Method: Close and reopen the production browser session.
    # ==========================================
    def test_session_survives_browser_restart(self):
        with self.browser() as (config, context, page):
            page.goto(config.booking_url)
            page.evaluate("sessionStorage.setItem('loginToken','mock-session'); localStorage.setItem('user','mock-local')")
            context.add_cookies([{"name": "mock-cookie", "value": "yes", "url": self.base}])
            save_session(context, page, self.root / "auth", origin_of(config.booking_url))
        with self.browser() as (config, context, page):
            page.goto(config.booking_url)
            self.assertEqual(page.evaluate("sessionStorage.getItem('loginToken')"), "mock-session")
            self.assertEqual(page.evaluate("localStorage.getItem('user')"), "mock-local")
            self.assertIn("mock-cookie", [c["name"] for c in context.cookies()])

    # ==========================================
    # Function: Prove known payment requests are blocked before reaching the server.
    # Method: Attempt a local fetch and require a client-side network failure.
    # ==========================================
    def test_payment_guard(self):
        with self.browser() as (config, _, page):
            page.goto(config.booking_url)
            result = page.evaluate("fetch('/venue/personal/orderImmediatelyPC',{method:'POST'}).then(()=>'sent').catch(()=>'blocked')")
            self.assertEqual(result, "blocked")

    # ==========================================
    # Function: Complete every required date rather than stopping after one order.
    # Method: Submit two independent goals and inspect native confirmation requests.
    # ==========================================
    def test_multiple_dates_all_complete(self):
        tomorrow = replace(self.target, date=(self.today + timedelta(days=1)).isoformat())
        goals = [Goal("today", "今天", [self.target]), Goal("tomorrow", "明天", [tomorrow])]
        with self.browser(goals=goals, targets=[], monitor_until_complete=True) as (config, _, page):
            requests = []
            page.on("request", lambda r: requests.append(r.url) if r.url.endswith("/venue/personal/ConfirmOrder") else None)
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["completed"], 2)
            self.assertEqual(len(requests), 2)
            self.assertEqual(len(self.journal.entries()), 2)

    # ==========================================
    # Function: Prefer the highest ranked candidate when all alternatives are free.
    # Method: List court two first and assert its order is selected.
    # ==========================================
    def test_goal_candidate_priority(self):
        preferred = replace(self.target, court="场地2", slots=["15:00-16:00"])
        goal = Goal("priority", "优先级", [preferred, self.target])
        with self.browser(goals=[goal], targets=[]) as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(result["goals"][0]["detail"]["court"], "场地2")
            self.assertEqual(result["goals"][0]["detail"]["slots"], ["15:00-16:00"])

    # ==========================================
    # Function: Avoid resubmitting completed goals after restarting.
    # Method: Reorder candidates, rerun, and require zero native submit requests.
    # ==========================================
    def test_plan_resume_skips_completed_goals(self):
        goal = Goal("resume", "恢复", [self.target, replace(self.target, court="场地2")])
        with self.browser(goals=[goal], targets=[]) as (config, _, page):
            run_reservation(page, config, self.journal, self.root / "first", submit=True)
            config.goals[0].candidates.reverse()
            requests = []
            page.on("request", lambda r: requests.append(r.url) if "ConfirmOrder" in r.url else None)
            result = run_reservation(page, config, self.journal, self.root / "second", submit=True)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(requests, [])

    # ==========================================
    # Function: Keep fulfilled goals while waiting on remaining unavailable goals.
    # Method: Bound one round and verify completion is partial and resumable.
    # ==========================================
    def test_partial_plan_does_not_claim_completion(self):
        unavailable = replace(self.target, slots=["13:00-14:00"])
        goals = [Goal("done", "可用", [self.target]), Goal("wait", "等待", [unavailable])]
        with self.browser(goals=goals, targets=[]) as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(result["status"], "pending")
            self.assertEqual(result["completed"], 1)
            self.assertEqual(result["total"], 2)

    # ==========================================
    # Function: Continue beyond the old timeout in all-goals mode.
    # Method: Count serial scans until a cooperative user stop instead of timeout.
    # ==========================================
    def test_until_complete_mode_respects_user_stop(self):
        goal = Goal("wait", "等待", [self.target])
        rounds = []
        with self.browser("soldout", goals=[goal], targets=[], monitor_until_complete=True) as (config, _, page):
            # ==========================================
            # Function: Observe monitoring rounds in the mock run.
            # Method: Accumulate progress messages to trigger a cooperative stop.
            # ==========================================
            def progress(event):
                rounds.append(event["message"])
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True,
                                     progress=progress, stop=lambda: rounds.count("等待空位，继续检查未完成目标") >= 2)
            self.assertEqual(result["status"], "stopped")
            self.assertGreaterEqual(rounds.count("等待空位，继续检查未完成目标"), 2)

    # ==========================================
    # Function: Do not count uncertain orders as fulfilled goals.
    # Method: Preserve unknown journal state and return an actionable partial result.
    # ==========================================
    def test_unknown_plan_needs_attention(self):
        goal = Goal("uncertain", "需要核对", [self.target])
        with self.browser("missing-id", goals=[goal], targets=[], monitor_until_complete=True) as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=True)
            self.assertEqual(result["status"], "needs_attention")
            self.assertEqual(result["completed"], 0)
            self.assertEqual(self.journal.record(config.goal_key(goal))["status"], "unknown")

    # ==========================================
    # Function: Preview scheduled plans immediately before the release time.
    # Method: Set a future start time and require prompt, non-submitting completion.
    # ==========================================
    def test_scheduled_plan_preview_is_immediate(self):
        import time
        goal = Goal("preview-now", "定时前预览", [self.target])
        future = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(seconds=4)).isoformat()
        with self.browser(goals=[goal], targets=[], start_at=future) as (config, _, page):
            start = time.monotonic()
            result = run_reservation(page, config, self.journal, self.root / "artifacts", submit=False)
            self.assertEqual(result["status"], "preview")
            self.assertLess(time.monotonic() - start, 3)
            self.assertEqual(page.evaluate("submitClicks"), 0)

    # ==========================================
    # Function: Keep lower-priority goals monitored while the first has no vacancy.
    # Method: Read both dates in one round and submit the available second goal.
    # ==========================================
    def test_unavailable_first_goal_does_not_block_others(self):
        later = replace(self.target, date=(self.today + timedelta(days=1)).isoformat())
        goals = [Goal("unavailable-first", "首个无空位", [self.target]), Goal("available-later", "另一日期", [later])]
        with self.browser("first-date-soldout", goals=goals, targets=[]) as (config, _, page):
            events = []
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True,
                                     progress=lambda event: events.append(event))
            self.assertEqual(result["completed"], 1)
            self.assertEqual(result["goals"][0]["status"], "pending")
            self.assertEqual(result["goals"][1]["status"], "submitted")
            self.assertTrue(result["goals"][0]["last_checked_at"])
            self.assertTrue(result["goals"][1]["last_checked_at"])
            queued = next(e for e in events if "本轮已检查 2 个日期" in e["message"])
            self.assertTrue(all(g["last_checked_at"] for g in queued["goals"]))

    # ==========================================
    # Function: Prevent duplicate date reads for many same-date targets.
    # Method: Scan five pending goals twice and require only one date refresh.
    # ==========================================
    def test_same_date_goals_share_reads_without_full_reload(self):
        goals = [Goal(f"shared-{i}", f"目标{i}", [self.target]) for i in range(5)]
        with self.browser("soldout", goals=goals, targets=[], monitor_until_complete=True) as (config, _, page):
            rounds, navigations = [], []
            page.on("framenavigated", lambda frame: navigations.append(frame.url) if frame == page.main_frame else None)
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True,
                                     progress=lambda e: rounds.append(e["message"]),
                                     stop=lambda: rounds.count("等待空位，继续检查未完成目标") >= 2)
            self.assertEqual(result["monitor"]["reads"], 2)
            self.assertEqual(page.evaluate("inventoryRequests"), 1)
            self.assertEqual(len(navigations), 1)
            self.assertTrue(all(g["check_count"] == 2 for g in result["goals"]))

    # ==========================================
    # Function: Recover a transient date timeout without downloading the booking page again.
    # Method: Fail one read before its native click, then require one document navigation and one order.
    # ==========================================
    def test_date_timeout_reuses_current_document(self):
        later = replace(self.target, date=(self.today + timedelta(days=1)).isoformat())
        goal = Goal('date-recovery', '日期读取恢复', [later])
        original, attempts, navigations = SjtuPage.choose_date, [], []

        # ==========================================
        # Function: Model a transient unfinished date read without altering browser state.
        # Method: Raise once and delegate subsequent attempts to the actual site adapter.
        # ==========================================
        def delayed_date(site, value):
            attempts.append(value)
            if len(attempts) == 1:
                raise GridNotReady('预约网格加载超时')
            return original(site, value)

        with self.browser(goals=[goal], targets=[], monitor_until_complete=True) as (config, _, page):
            page.on('framenavigated', lambda frame: navigations.append(frame.url) if frame == page.main_frame else None)
            with patch.object(SjtuPage, 'choose_date', delayed_date):
                result = run_reservation(page, config, self.journal, self.root / 'artifacts', True)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(len(navigations), 1)
            self.assertEqual(len(attempts), 2)
            self.assertEqual(page.evaluate('submitClicks'), 1)

    # ==========================================
    # Function: Respect a native read throttle without falsely submitting stale data.
    # Method: Return one 429 response, then allow a later monitored read to succeed.
    # ==========================================
    def test_429_enters_global_backoff_then_recovers(self):
        later = replace(self.target, date=(self.today + timedelta(days=1)).isoformat())
        goal = Goal("throttle", "限流恢复", [later])
        with self.browser(goals=[goal], targets=[], monitor_until_complete=True) as (config, _, page):
            calls, events = [], []
            # ==========================================
            # Function: Simulate one native inventory rate limit.
            # Method: Supply Retry-After on the first read and a valid response afterward.
            # ==========================================
            def inventory(route):
                calls.append(route.request.url)
                route.fulfill(status=429 if len(calls) == 1 else 200,
                              headers={"retry-after": "120", "content-type": "application/json"},
                              body='{"code":0,"data":[]}')
            page.route("**/manage/fieldDetail/queryFieldSituation", inventory)
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True,
                                     progress=lambda e: events.append(e))
            self.assertEqual(result["status"], "completed")
            self.assertGreaterEqual(len(calls), 2)
            self.assertTrue(any(e["monitor"]["cooling_down"] and "120" in e["monitor"]["reason"] for e in events))

    # ==========================================
    # Function: Stop instead of hammering a site that forbids monitoring.
    # Method: Return HTTP 403 on the first native date read and require no order.
    # ==========================================
    def test_403_pauses_all_goal_monitoring(self):
        later = replace(self.target, date=(self.today + timedelta(days=1)).isoformat())
        with self.browser(goals=[Goal("denied", "拒绝访问", [later])], targets=[], monitor_until_complete=True) as (config, _, page):
            calls = []
            # ==========================================
            # Function: Simulate the site's explicit refusal of an availability read.
            # Method: Count reads and return a forbidden response without retry advice.
            # ==========================================
            def forbidden(route):
                calls.append(route.request.url)
                route.fulfill(status=403, body="Forbidden")
            page.route("**/manage/fieldDetail/queryFieldSituation", forbidden)
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True)
            self.assertEqual(result["status"], "access_limited")
            self.assertEqual(len(calls), 1)
            self.assertEqual(self.journal.entries(), [])

    # ==========================================
    # Function: Avoid interpreting the previous sport's grid as newly loaded data.
    # Method: Delay the calendar response while no overlay is visible, then await inventory.
    # ==========================================
    def test_sport_switch_waits_for_chained_native_reads(self):
        with self.browser("sport-switch") as (config, _, page):
            site = SjtuPage(page, config)
            page.goto(config.booking_url)
            initial = site.ready()
            self.assertEqual(initial["sport"], "排练厅")
            self.assertIsNone(candidate_cells(initial, self.target))
            shot = site.choose_sport()
            self.assertFalse(shot["loading"])
            self.assertIsNotNone(candidate_cells(shot, self.target))
            self.assertEqual(page.evaluate("[calendarRequests, inventoryRequests, submitClicks]"), [1, 1, 0])

    # ==========================================
    # Function: Use the settled sport snapshot throughout the actual multi-goal runner.
    # Method: Submit one local order after the delayed calendar and inventory chain.
    # ==========================================
    def test_plan_survives_sport_switch_loading_race(self):
        goal = Goal("chained-read", "异步切换", [self.target])
        with self.browser("sport-switch", goals=[goal], targets=[], monitor_until_complete=True) as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(page.evaluate("[submitClicks,payClicks]"), [1, 0])

    # ==========================================
    # Function: Recover from a transient initial loading timeout without stopping watchers.
    # Method: Fail the first sport read and require global cooldown plus one local order.
    # ==========================================
    def test_initial_grid_timeout_enters_backoff_then_recovers(self):
        goal = Goal("loading-retry", "加载重试", [self.target])
        original, attempts, events = SjtuPage.choose_sport, [], []

        # ==========================================
        # Function: Simulate a single unfinished native grid read.
        # Method: Raise before returning the first snapshot, then delegate normally.
        # ==========================================
        def delayed_sport(site):
            attempts.append(True)
            if len(attempts) == 1:
                raise GridNotReady("预约网格加载超时")
            return original(site)

        with self.browser(goals=[goal], targets=[], monitor_until_complete=True) as (config, _, page):
            with patch.object(SjtuPage, "choose_sport", delayed_sport):
                result = run_reservation(page, config, self.journal, self.root / "artifacts", True,
                                         progress=events.append)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(attempts), 2)
            self.assertTrue(any(e["monitor"]["cooling_down"] and "当前任务退避 30 秒" in e["message"] for e in events))
            self.assertEqual(page.evaluate("submitClicks"), 1)

    # ==========================================
    # Function: Recover a loading race immediately before submission without arming a latch.
    # Method: Fail once after opening mock terms, then reload under the shared read budget.
    # ==========================================
    def test_pre_submit_grid_timeout_recovers_without_duplicate_order(self):
        goal = Goal("before-submit-retry", "提交前加载", [self.target])
        original, attempts, events = SjtuPage.prepare_submit, [], []

        # ==========================================
        # Function: Delay the final DOM verification once before the durable latch.
        # Method: Open mock terms normally, then signal a recoverable read failure.
        # ==========================================
        def delayed_terms(site, target):
            button = original(site, target)
            attempts.append(True)
            if len(attempts) == 1:
                raise GridNotReady("预约网格未加载完成")
            return button

        with self.browser(goals=[goal], targets=[], monitor_until_complete=True) as (config, _, page):
            submissions = []
            page.on("request", lambda request: submissions.append(request.url) if request.url.endswith("/ConfirmOrder") else None)
            with patch.object(SjtuPage, "prepare_submit", delayed_terms):
                result = run_reservation(page, config, self.journal, self.root / "artifacts", True,
                                         progress=events.append)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(attempts), 2)
            self.assertEqual(len(submissions), 1)
            self.assertEqual(len(self.journal.entries()), 1)
            self.assertTrue(any(e["monitor"]["cooling_down"] for e in events))

    # ==========================================
    # Function: Refuse to use a permanently covered grid while keeping a bounded result.
    # Method: Hold the loading overlay visible until a real DOM wait timeout.
    # ==========================================
    def test_stuck_loading_returns_pending_without_order(self):
        goal = Goal("stuck-grid", "加载未结束", [self.target])
        with self.browser(goals=[goal], targets=[], action_timeout_ms=300) as (config, _, page):
            page.add_init_script("""document.addEventListener('DOMContentLoaded', () => {
                document.querySelector('.el-loading-mask').style.display = 'block';
            });""")
            result = run_reservation(page, config, self.journal, self.root / "artifacts", False)
            self.assertEqual(result["status"], "pending")
            self.assertTrue(result["monitor"]["cooling_down"])
            self.assertEqual(page.evaluate("submitClicks"), 0)
            self.assertEqual(self.journal.entries(), [])

    # ==========================================
    # Function: Resolve unrestricted courts from live monitoring data rather than manual names.
    # Method: Make court one unavailable and require one order on the actual court two.
    # ==========================================
    def test_any_court_resolves_after_monitoring_starts(self):
        goal = Goal("any-court", "任意空位", [replace(self.target, court=ANY_COURT)])
        with self.browser(goals=[goal], targets=[], monitor_until_complete=True) as (config, _, page):
            page.add_init_script("""document.addEventListener('DOMContentLoaded', () => {
                for (const row of document.querySelectorAll('.inner-seat-wrapper > div')) {
                    row.querySelector('.inner-seat').className = 'inner-seat bought-seat';
                }
            });""")
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["goals"][0]["detail"]["court"], "场地2")
            self.assertEqual(page.evaluate("submitClicks"), 1)

    # ==========================================
    # Function: Honor candidate time priority even when the court is unrestricted.
    # Method: Prefer a later listed hour on actual court one over a lower-ranked exact court.
    # ==========================================
    def test_any_court_retains_candidate_priority(self):
        preferred = replace(self.target, court=ANY_COURT, slots=["15:00-16:00"])
        goal = Goal("any-priority", "任意场地优先级", [preferred, self.target])
        with self.browser(goals=[goal], targets=[]) as (config, _, page):
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True)
            self.assertEqual(result["goals"][0]["detail"]["court"], "场地1")
            self.assertEqual(result["goals"][0]["detail"]["slots"], ["15:00-16:00"])

    # ==========================================
    # Function: Keep scheduled waiting free of availability requests until its start time.
    # Method: Stop from the scheduling progress callback and require no booking navigation.
    # ==========================================
    def test_scheduled_wait_does_not_read_inventory(self):
        goal = Goal("scheduled-no-read", "定时未开始", [self.target])
        future = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(hours=1)).isoformat()
        with self.browser(goals=[goal], targets=[], start_at=future) as (config, _, page):
            events, requests = [], []
            page.on("request", lambda request: requests.append(request.url))
            result = run_reservation(page, config, self.journal, self.root / "artifacts", True,
                                     progress=events.append,
                                     stop=lambda: any("尚未读取空位" in event["message"] for event in events))
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(requests, [])
            self.assertEqual(self.journal.entries(), [])


if __name__ == "__main__":
    unittest.main()
