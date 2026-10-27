"""Static catalog caching and native-controller guarantees, without live accounts."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from autoreserve.catalog import ANY_COURT, DirectoryCache, DirectoryClient, DirectoryUnavailable, LoginRequired, booking_url
from autoreserve.config import Config, Goal, Target, load_config, save_config
from autoreserve.desktop_controller import DesktopController
from autoreserve.state import Journal, private_json


# ==========================================
# Function: Build a complete static venue/project catalog for offline tests.
# Method: Include different projects with no date, inventory, or user fields.
# ==========================================
def directory_fixture():
    return {"version": 1, "created_at": "2026-10-26T00:00:00+00:00", "venues": [
        {"id": "student", "name": "学生中心", "campus": "闵行", "sports": [
            {"id": "gym", "name": "健身房"}, {"id": "dance", "name": "舞蹈厅"}]},
        {"id": "stadium", "name": "体育馆", "campus": "徐汇", "sports": [{"id": "ball", "name": "羽毛球"}]},
    ]}


# ==========================================
# Function: Create a valid plan whose journal identity can be tested across switches.
# Method: Use a future date, a real-format route, and one unrestricted court candidate.
# ==========================================
def native_config():
    day = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=1)).date().isoformat()
    return Config(booking_url=booking_url("student", "健身房"), venue="学生中心", sport="健身房",
                  goals=[Goal("native-one", "健身目标", [Target(day, ANY_COURT, ["14:00-15:00"])])],
                  headless=True, monitor_until_complete=True)


# ==========================================
# Function: Verify cache misses read metadata once while cache hits stay offline.
# Method: Inject deterministic static responses and fail any unexpected fetch.
# ==========================================
class DirectoryTests(unittest.TestCase):
    # ==========================================
    # Function: Fetch all pages and project details without requesting availability.
    # Method: Record endpoint calls and require no calls on the second ensure.
    # ==========================================
    def test_cache_miss_once_and_hit_zero_reads(self):
        catalog, calls = directory_fixture(), []

        # ==========================================
        # Function: Serve paginated metadata only.
        # Method: Fail the test if any slot or order endpoint is attempted.
        # ==========================================
        def fetch(endpoint, values):
            calls.append((endpoint, values))
            if endpoint.endswith("/list"):
                venue = catalog["venues"][values["pageNum"] - 1]
                return {"rows": [{"venueId": venue["id"], "venueName": venue["name"], "campusName": venue["campus"]}], "total": 2}
            self.assertTrue(endpoint.endswith("/queryVenueById"))
            venue = next(v for v in catalog["venues"] if v["id"] == values["id"])
            return {"data": {"motionTypes": venue["sports"]}}

        with tempfile.TemporaryDirectory() as d:
            cache = DirectoryCache(d)
            wait = Mock()
            result = cache.ensure(fetch, wait)
            self.assertEqual(result["venues"], catalog["venues"])
            self.assertEqual(len(calls), 4)
            self.assertEqual(wait.call_count, 4)
            self.assertEqual(cache.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(cache.ensure(Mock(side_effect=AssertionError("network")), Mock()), result)
            self.assertEqual(len(calls), 4)

    # ==========================================
    # Function: Avoid publishing partial metadata as a complete catalog.
    # Method: Fail a detail response and require no cache file.
    # ==========================================
    def test_incomplete_directory_is_not_cached(self):
        fetch = Mock(side_effect=[{"rows": [{"venueId": "x", "venueName": "X"}], "total": 1}, {"data": None}])
        with tempfile.TemporaryDirectory() as d:
            cache = DirectoryCache(d)
            with self.assertRaisesRegex(RuntimeError, "不完整"):
                cache.ensure(fetch, lambda: None)
            self.assertFalse(cache.path.exists())

    # ==========================================
    # Function: Keep corrupt cache handling offline and explicit.
    # Method: Leave an invalid existing file untouched and prohibit fetches.
    # ==========================================
    def test_corrupt_existing_cache_does_not_refresh(self):
        with tempfile.TemporaryDirectory() as d:
            cache = DirectoryCache(d)
            cache.path.write_text("broken")
            fetch = Mock()
            with self.assertRaisesRegex(RuntimeError, "缓存格式损坏"):
                cache.ensure(fetch, Mock())
            fetch.assert_not_called()
            self.assertEqual(cache.path.read_text(), "broken")

    # ==========================================
    # Function: Cancel discovery without committing partial metadata.
    # Method: Stop before the first fetch and require no cache write.
    # ==========================================
    def test_stop_before_directory_read(self):
        with tempfile.TemporaryDirectory() as d:
            cache, fetch = DirectoryCache(d), Mock()
            with self.assertRaises(InterruptedError):
                cache.ensure(fetch, Mock(), stop=lambda: True)
            fetch.assert_not_called()
            self.assertFalse(cache.path.exists())

    # ==========================================
    # Function: Prevent directory code from accessing inventory or submission endpoints.
    # Method: Exercise the endpoint allowlist before any browser evaluation.
    # ==========================================
    def test_directory_endpoint_allowlist(self):
        page = Mock()
        client = DirectoryClient(page)
        for endpoint in ("/manage/fieldDetail/queryFieldSituation", "/venue/personal/ConfirmOrder", "https://other.invalid/"):
            with self.assertRaises(ValueError):
                client.fetch(endpoint, {})
        page.evaluate.assert_not_called()

    # ==========================================
    # Function: Surface expired authentication instead of treating it as empty metadata.
    # Method: Test both HTTP and application-level login failures.
    # ==========================================
    def test_authentication_must_be_server_confirmed(self):
        page = Mock()
        client = DirectoryClient(page)
        for response in ({"status": 200, "data": {"code": 401}}, {"status": 403, "data": None},
                         {"status": 200, "data": {"code": 0, "data": {}}}):
            page.evaluate.return_value = response
            with self.assertRaises(LoginRequired):
                client.authenticated()
        page.evaluate.return_value = {"status": 200, "data": {"code": 0, "data": {"loginName": "mock-user"}}}
        self.assertTrue(client.authenticated())

    # ==========================================
    # Function: Recover transient aborts using a longer, bounded second request.
    # Method: Return browser transport errors before valid metadata and skip only the retry clock in unit tests.
    # ==========================================
    def test_directory_transport_retry_has_bounded_larger_timeout(self):
        for error in ('timeout','network','aborted'):
            page, progress = Mock(), Mock()
            response = {'status':200,'data':{'code':0,'rows':[],'total':0}}
            page.evaluate.side_effect = [{'error':error},response]
            with self.subTest(error=error), patch('autoreserve.catalog.time.monotonic',side_effect=[0,3]):
                self.assertEqual(DirectoryClient(page,progress=progress).fetch('/manage/venue/list',{}),response['data'])
            self.assertEqual([call.args[1]['timeout'] for call in page.evaluate.call_args_list],[30000,60000])
            self.assertEqual(page.evaluate.call_count,2)
            progress.assert_called_once()

    # ==========================================
    # Function: Preserve authentication and stop after persistent metadata transport failures.
    # Method: Exhaust exactly two attempts and require an actionable directory error instead of login rejection.
    # ==========================================
    def test_directory_retry_exhaustion_is_not_expired_login(self):
        page = Mock()
        page.evaluate.return_value = {'error':'timeout'}
        with patch('autoreserve.catalog.time.monotonic',side_effect=[0,3]):
            with self.assertRaisesRegex(DirectoryUnavailable,'已尝试 2 次.*登录保留'):
                DirectoryClient(page).fetch('/manage/venue/list',{})
        self.assertEqual(page.evaluate.call_count,2)

    # ==========================================
    # Function: Respect explicit login rejection, throttles and invalid server responses without a transport retry.
    # Method: Check HTTP and application statuses plus malformed JSON with a strict one-call count.
    # ==========================================
    def test_directory_server_errors_are_not_retried(self):
        for status,data,error in ((401,None,LoginRequired),(403,None,LoginRequired),
                                  (429,None,DirectoryUnavailable),(503,None,DirectoryUnavailable),
                                  (500,None,DirectoryUnavailable),(200,None,DirectoryUnavailable),
                                  (200,{'code':401},LoginRequired),(200,{'code':429},RuntimeError)):
            page = Mock()
            page.evaluate.return_value = {'status':status,'data':data}
            with self.subTest(status=status,data=data), self.assertRaises(error):
                DirectoryClient(page).fetch('/system/user/currentUser')
            page.evaluate.assert_called_once()

    # ==========================================
    # Function: Honor cancellation between a failed metadata read and its only retry.
    # Method: Request stop from the progress callback and forbid another browser evaluation.
    # ==========================================
    def test_directory_retry_is_cancelable(self):
        page, stopped = Mock(), []
        page.evaluate.return_value = {'error':'timeout'}
        client = DirectoryClient(page,stop=lambda:bool(stopped),progress=stopped.append)
        with self.assertRaises(InterruptedError):
            client.fetch('/manage/venue/list',{})
        page.evaluate.assert_called_once()

    # ==========================================
    # Function: Preserve encoded route segments for selected cached projects.
    # Method: Build a route that satisfies the shared strict configuration validator.
    # ==========================================
    def test_directory_route_is_valid(self):
        config = native_config()
        config.validate(require_targets=True)
        self.assertIn("%E5%81%A5", config.booking_url)


# ==========================================
# Function: Verify native profiles, cache hits, and court-name persistence stay offline.
# Method: Patch browser launch so unintended network work fails immediately.
# ==========================================
class NativeControllerTests(unittest.TestCase):
    # ==========================================
    # Function: Keep construction and cached catalog selection completely offline.
    # Method: Require no browser launch and preserve the catalog file bytes.
    # ==========================================
    def test_cached_startup_and_switch_are_offline(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            config = native_config()
            save_config(config, root / "config.json")
            private_json(root / "auth/catalog.json", directory_fixture())
            before = (root / "auth/catalog.json").read_bytes()
            with patch("autoreserve.desktop_controller.open_browser", side_effect=AssertionError("browser")):
                controller = DesktopController(root / "config.json", root / "auth")
                workspace = load_config(root / "config.json")
                controller.start("catalog")
                selected = controller.select_context("student", "舞蹈厅")
                self.assertEqual(selected.sport, "舞蹈厅")
                self.assertEqual(len(selected.goals), 1)
                restored = controller.select_context("student", "健身房")
            self.assertEqual(restored.venue, config.venue)
            self.assertEqual(asdict(load_config(root / "config.json")), asdict(workspace))
            self.assertEqual((root / "auth/catalog.json").read_bytes(), before)

    # ==========================================
    # Function: Preserve completed orders and route identities when changing projects.
    # Method: Complete one journal goal, switch away, and restore the exact original route.
    # ==========================================
    def test_context_switch_preserves_completed_order(self):
        with tempfile.TemporaryDirectory() as d:
            root, config = Path(d), native_config()
            # Retain a legacy captured route rather than silently canonicalizing it.
            config.booking_url = "https://sports.sjtu.edu.cn/pc/#/apointmentDetails/1/student/全部/0"
            goal = config.goals[0]
            target = replace(goal.candidates[0], court="场地1")
            save_config(config, root / "config.json")
            private_json(root / "auth/catalog.json", directory_fixture())
            journal = Journal(root / "auth/journal.sqlite3")
            journal.arm(replace(config.for_goal(goal), targets=[target]), target, config.goal_key(goal))
            journal.update(config.goal_key(goal), "submitted", {"order_id": "LOCAL-NATIVE"})
            journal.close()
            controller = DesktopController(root / "config.json", root / "auth")
            controller.select_context("stadium", "羽毛球")
            restored = controller.select_context("student", "健身房")
            self.assertEqual(load_config(root / "config.json").booking_url, config.booking_url)
            self.assertEqual(controller.state()["goals"][0]["status"], "submitted")

    # ==========================================
    # Function: Remember only project-scoped court names learned during monitoring.
    # Method: Publish a snapshot and verify a different project cannot inherit its choices.
    # ==========================================
    def test_monitored_court_names_are_scoped(self):
        with tempfile.TemporaryDirectory() as d:
            root, config = Path(d), native_config()
            save_config(config, root / "config.json")
            controller = DesktopController(root / "config.json", root / "auth")
            self.assertEqual(controller.court_names(config), [])
            controller.progress({"message": "监测", "snapshot": {"venue": config.venue, "sport": config.sport, "columns": ["场地1", "场地2"]}})
            self.assertEqual(controller.court_names(config), ["场地1", "场地2"])
            self.assertEqual(controller.court_names(replace(config, sport="舞蹈厅")), [])
            self.assertFalse(controller.cache.path.exists())

    # ==========================================
    # Function: Exclude manual availability scans from the native client workflow.
    # Method: Reject scan/preview actions before starting any background worker.
    # ==========================================
    def test_native_client_has_no_pre_monitor_scan(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            controller = DesktopController(root / "config.json", root / "auth")
            for operation in ("scan", "preview"):
                with self.assertRaisesRegex(ValueError, "启动监测后"):
                    controller.start(operation)
            self.assertFalse(controller.busy)
