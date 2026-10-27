"""Bounded read-only recovery and stale-page diagnostics without a live account."""

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from autoreserve.catalog import LoginRequired
from autoreserve.config import HOME_URL, save_config
from autoreserve.desktop_controller import DesktopController
from autoreserve.monitoring import AccessDenied, GridReadStalled, ReadDeferred
from autoreserve.sjtu import SjtuPage
from test_catalog import native_config


# ==========================================
# Function: Verify that browser replacement is bounded and cannot discard the order journal.
# Method: Keep actual temporary configuration/SQLite and mock only browser transport and the reservation runner.
# ==========================================
class RecoveryTests(unittest.TestCase):
    # ==========================================
    # Function: Create a real controller isolated from user configuration and account state.
    # Method: Use the existing valid workspace fixture with unlimited monitoring and no network.
    # ==========================================
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.config=replace(native_config(),max_poll_seconds=0)
        save_config(self.config,self.root/'config.json')
        self.controller=DesktopController(self.root/'config.json',self.root/'auth')
        self.lifecycle=[]
        self.browsers=patch('autoreserve.desktop_controller.open_browser',side_effect=self.browser).start()
        self.auth=patch('autoreserve.desktop_controller.DirectoryClient.authenticated',return_value=True).start()
        self.save=patch('autoreserve.desktop_controller.save_session').start()
        patch('autoreserve.desktop_controller.evidence').start()

    # ==========================================
    # Function: Model fresh browser ownership while retaining actual controller and journal behavior.
    # Method: Record close-before-open sequencing and provide only the page APIs used by recovery orchestration.
    # ==========================================
    @contextmanager
    def browser(self, *args, **kwargs):
        page=Mock(url=HOME_URL)
        page.is_closed.return_value=False
        self.lifecycle.append('open')
        try:
            yield Mock(),page
        finally:
            self.lifecycle.append('close')

    # ==========================================
    # Function: Restore transport boundaries and delete only test-owned files.
    # Method: Stop mocks after synchronous monitor completion so SQLite handles are already closed.
    # ==========================================
    def tearDown(self):
        patch.stopall()
        self.temp.cleanup()

    # ==========================================
    # Function: Retain a confirmed order when a later grid stall forces browser replacement.
    # Method: Write a real journal entry on the first attempt and verify it from the reopened runner.
    # ==========================================
    def test_recovery_preserves_journal_and_closes_old_browser(self):
        goal=self.config.goals[0]
        key=self.config.goal_key(goal)
        calls=[]

        # ==========================================
        # Function: Simulate a completed order followed by a read-only stall on another pending goal.
        # Method: Preserve the same SQLite key while the browser lifecycle is replaced.
        # ==========================================
        def run(page,config,journal,*args,**kwargs):
            calls.append(page)
            if len(calls)==1:
                journal.arm(config,goal.candidates[0],key)
                journal.update(key,'submitted',{'order_id':'confirmed-local-order'})
                raise GridReadStalled('连续网格超时')
            self.assertEqual(journal.record(key)['detail']['order_id'],'confirmed-local-order')
            return {'status':'completed','message':'已完成'}

        with patch('autoreserve.desktop_controller.run_reservation',side_effect=run):
            self.controller.monitor_with_recovery(self.config,self.root/'artifacts')
        self.assertEqual(self.controller.result['status'],'completed')
        self.assertEqual(self.lifecycle,['open','close','open','close'])
        self.assertEqual(self.save.call_count,2)
        self.assertIsNot(calls[0],calls[1])

    # ==========================================
    # Function: Stop a permanently stuck site instead of restarting indefinitely.
    # Method: Fail both browsers and require exactly one replacement plus an actionable final error.
    # ==========================================
    def test_replacement_is_limited_to_one(self):
        with patch('autoreserve.desktop_controller.run_reservation',side_effect=GridReadStalled('网格卡住')):
            with self.assertRaisesRegex(RuntimeError,'重建浏览器后仍连续读取超时'):
                self.controller.monitor_with_recovery(self.config,self.root/'artifacts')
        self.assertEqual(self.browsers.call_count,2)
        self.assertEqual(self.lifecycle,['open','close','open','close'])

    # ==========================================
    # Function: Respect authentication failures and explicit access denial without replacing browsers.
    # Method: Propagate the original error and leave saved session state untouched.
    # ==========================================
    def test_authentication_and_access_denial_do_not_reconnect(self):
        for error in (LoginRequired('HTTP 401'),AccessDenied('HTTP 403')):
            self.browsers.reset_mock()
            self.save.reset_mock()
            with self.subTest(error=error), patch('autoreserve.desktop_controller.run_reservation',side_effect=error):
                with self.assertRaises(type(error)):
                    self.controller.monitor_with_recovery(self.config,self.root/'artifacts')
            self.browsers.assert_called_once()
            self.save.assert_not_called()

    # ==========================================
    # Function: Honor user cancellation before another browser can open.
    # Method: Request stop on the stalled attempt and verify no recovery startup or session rewrite.
    # ==========================================
    def test_stop_cancels_replacement(self):
        # ==========================================
        # Function: Reproduce a user stopping at the same instant a grid timeout is reported.
        # Method: Set the controller's actual event before raising the recovery signal.
        # ==========================================
        def stopped(*args,**kwargs):
            self.controller.stop_event.set()
            raise GridReadStalled('网格超时')

        with patch('autoreserve.desktop_controller.run_reservation',side_effect=stopped):
            with self.assertRaises(InterruptedError):
                self.controller.monitor_with_recovery(self.config,self.root/'artifacts')
        self.browsers.assert_called_once()
        self.save.assert_not_called()

    # ==========================================
    # Function: Preserve an ordinary timed task's original deadline through browser replacement.
    # Method: Advance a deterministic clock and inspect the smaller budget passed to the replacement runner.
    # ==========================================
    def test_recovery_does_not_reset_finite_monitoring_budget(self):
        config=replace(self.config,max_poll_seconds=100,monitor_until_complete=False)
        result={'status':'completed','message':'已完成'}
        with patch('autoreserve.desktop_controller.time.monotonic',side_effect=[10,30]), \
                patch('autoreserve.desktop_controller.run_reservation',side_effect=[GridReadStalled('卡住'),result]) as run:
            self.controller.monitor_with_recovery(config,self.root/'artifacts')
        self.assertEqual(run.call_args_list[0].args[1].max_poll_seconds,100)
        self.assertEqual(run.call_args_list[1].args[1].max_poll_seconds,80)


# ==========================================
# Function: Validate page-state invalidation and server-error classification before DOM waiting.
# Method: Exercise the real adapter against event-boundary mocks without browser/network dependencies.
# ==========================================
class PageStateTests(unittest.TestCase):
    # ==========================================
    # Function: Drop unfinished requests belonging to an old document when the main frame commits navigation.
    # Method: Track a native read, navigate and prove a late old completion cannot corrupt the new page.
    # ==========================================
    def test_navigation_clears_old_pending_requests(self):
        page=Mock(url=HOME_URL)
        site=SjtuPage(page,native_config())
        request=Mock(url='https://sports.sjtu.edu.cn/manage/fieldDetail/queryFieldSituation',failure=None)
        site._read_started(request)
        self.assertEqual(len(site._pending_reads),1)
        site._navigated(page.main_frame)
        request.failure='net::ERR_ABORTED'
        site._read_finished(request)
        self.assertFalse(site._pending_reads)
        self.assertIsNone(site._read_error)

    # ==========================================
    # Function: Distinguish expired sessions and explicit server refusals from a slow grid.
    # Method: Deliver HTTP feedback and require immediate typed errors without DOM evaluation.
    # ==========================================
    def test_server_errors_do_not_wait_for_grid_timeout(self):
        for status,error in ((401,LoginRequired),(403,AccessDenied),(429,ReadDeferred),(503,ReadDeferred)):
            page=Mock(url=HOME_URL)
            site=SjtuPage(page,native_config())
            site._read_response(Mock(url='https://sports.sjtu.edu.cn/manage/fieldDetail/queryFieldSituation',status=status))
            with self.subTest(status=status),self.assertRaises(error):
                site.loaded_snapshot()
            page.evaluate.assert_not_called()
