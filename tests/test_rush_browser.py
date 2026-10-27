"""Real release gating and stale-stock regressions against a loopback-only booking site."""

from datetime import datetime, timedelta
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from autoreserve.config import Goal, RushSettings
from autoreserve.monitoring import ReadPacer
from autoreserve.runner import run_reservation
from autoreserve.sjtu import SjtuPage
import test_browser as fixtures


# ==========================================
# Function: Verify production rush scheduling without touching an account or live venue.
# Method: Reuse isolated Chromium fixtures with the real shared limiter and short future releases.
# ==========================================
@unittest.skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1', '设置 RUN_BROWSER_TESTS=1 启用抢购浏览器测试')
class RushBrowserTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.BrowserTests.tearDownClass.__func__)
    setUp = fixtures.BrowserTests.setUp
    tearDown = fixtures.BrowserTests.tearDown
    browser = fixtures.BrowserTests.browser

    # ==========================================
    # Function: Create a near-future ordinary goal whose available stock is already visible before release.
    # Method: Disable network clock estimation to compare browser operations to an exact local deadline.
    # ==========================================
    def timed_goal(self):
        release = datetime.now(ZoneInfo('Asia/Shanghai')) + timedelta(seconds=4)
        goal = Goal('timed', '限时预约', [self.target], rush=RushSettings(
            enabled=True, at=release.isoformat(), prewarm_seconds=5, sync_clock=False))
        return goal, release.timestamp()

    # ==========================================
    # Function: Prepare early but select and submit only after release, exactly once.
    # Method: Observe real DOM selection, order requests and explicit authentication preparation.
    # ==========================================
    def test_release_gates_selection_and_discards_prewarm_stock(self):
        goal, release = self.timed_goal()
        events, selections, submits, checks = [], [], [], []
        original = SjtuPage.select

        # ==========================================
        # Function: Record the instant production selection begins.
        # Method: Delegate unchanged to the native DOM adapter after sampling wall time.
        # ==========================================
        def select(site, target):
            selections.append(time.time())
            return original(site, target)

        with self.browser(goals=[goal], targets=[]) as (config, _, page), \
                patch('autoreserve.runner.ReadPacer', ReadPacer), patch.object(SjtuPage, 'select', select):
            page.on('request', lambda request: submits.append(time.time()) if request.url.endswith('/ConfirmOrder') else None)
            result = run_reservation(page, config, self.journal, self.root / 'artifacts', True, events.append,
                                     prepare_auth=lambda: checks.append(time.time()))
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(len(submits), 1)
            self.assertTrue(selections and min(selections) >= release)
            self.assertGreaterEqual(submits[0], release)
            self.assertEqual(len(checks), 1)
            self.assertLess(checks[0], release)
            self.assertTrue(any('已预热' in event['message'] for event in events))
            self.assertGreaterEqual(page.evaluate('inventoryRequests'), 1)
            self.assertEqual(page.evaluate('payClicks'), 0)

    # ==========================================
    # Function: Reject inventory that was available during preparation but sold out at release.
    # Method: Change the local document's availability rule at the exact release and stop after the first fresh check.
    # ==========================================
    def test_stale_prewarm_availability_cannot_submit(self):
        goal, release = self.timed_goal()
        events, submits = [], []
        document = (Path(__file__).resolve().parents[1] / 'demo/index.html').read_text()
        needle = "scenario !== 'soldout' &&"
        self.assertIn(needle, document)
        document = document.replace(needle, needle + f' Date.now() < {release * 1000:.3f} &&')
        with self.browser(goals=[goal], targets=[]) as (config, _, page), patch('autoreserve.runner.ReadPacer', ReadPacer):
            page.route(self.base + '/?scenario=success', lambda route: route.fulfill(content_type='text/html', body=document))
            page.on('request', lambda request: submits.append(request.url) if request.url.endswith('/ConfirmOrder') else None)
            result = run_reservation(page, config, self.journal, self.root / 'artifacts', True, events.append,
                                     stop=lambda: any(event['message'].startswith('等待空位，') for event in events))
            self.assertEqual(result['status'], 'stopped')
            self.assertEqual(submits, [])
            self.assertGreaterEqual(page.evaluate('inventoryRequests'), 1)
            self.assertEqual(page.locator('.selected-seat').count(), 0)
            self.assertEqual(self.journal.entries(), [])

    # ==========================================
    # Function: Keep server throttling authoritative during the accelerated first second.
    # Method: Return Retry-After on the first release-time inventory request and stop while the shared budget cools down.
    # ==========================================
    def test_first_second_429_stops_burst_and_submission(self):
        goal, release = self.timed_goal()
        events, calls, submits = [], [], []

        # ==========================================
        # Function: Simulate a real server's release-time rate limit.
        # Method: Record the request instant and return a native inventory 429 response.
        # ==========================================
        def throttle(route):
            calls.append(time.time())
            route.fulfill(status=429, headers={'retry-after': '5', 'content-type': 'application/json'}, body='{}')

        with self.browser(goals=[goal], targets=[]) as (config, _, page), patch('autoreserve.runner.ReadPacer', ReadPacer):
            page.route('**/manage/fieldDetail/queryFieldSituation', throttle)
            page.on('request', lambda request: submits.append(request.url) if request.url.endswith('/ConfirmOrder') else None)
            result = run_reservation(page, config, self.journal, self.root / 'artifacts', True, events.append,
                                     stop=lambda: any(event['monitor']['cooling_down'] for event in events))
            self.assertEqual(result['status'], 'stopped')
            self.assertEqual(len(calls), 1)
            self.assertGreaterEqual(calls[0], release)
            self.assertEqual(submits, [])
            self.assertTrue(any('429' in event['monitor']['reason'] for event in events))

    # ==========================================
    # Function: Let cancellation interrupt the prepared wait without ordering from early stock.
    # Method: Stop as soon as the real prewarm completion event is emitted.
    # ==========================================
    def test_cancel_during_prepared_wait(self):
        goal, _ = self.timed_goal()
        events = []
        with self.browser(goals=[goal], targets=[]) as (config, _, page), patch('autoreserve.runner.ReadPacer', ReadPacer):
            result = run_reservation(page, config, self.journal, self.root / 'artifacts', True, events.append,
                                     stop=lambda: any('已预热' in event['message'] for event in events))
            self.assertEqual(result['status'], 'stopped')
            self.assertEqual(page.evaluate('submitClicks'), 0)
            self.assertEqual(page.locator('.selected-seat').count(), 0)
            self.assertEqual(self.journal.entries(), [])
