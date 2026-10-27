"""Real Chromium metadata/login workflows against a counted loopback-only server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from autoreserve.browser import open_browser, save_session
from autoreserve.catalog import DirectoryCache, DirectoryClient, LoginRequired, block_availability
from autoreserve.config import save_config
from autoreserve.desktop_controller import DesktopController
from autoreserve.state import private_json
from test_catalog import directory_fixture, native_config


# ==========================================
# Function: Serve static names and homepage login responses with request accounting.
# Method: Count every request so forbidden pre-monitor inventory reads are observable.
# ==========================================
class DirectoryHandler(BaseHTTPRequestHandler):
    # ==========================================
    # Function: Return deterministic local JSON responses.
    # Method: Use explicit content type and length without contacting any remote service.
    # ==========================================
    def reply(self, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ==========================================
    # Function: Serve the homepage and authenticated identity check.
    # Method: Reproduce a stale token's misleading badge without requesting any inventory.
    # ==========================================
    def do_GET(self):
        self.server.calls.append(self.path)
        if self.path == "/system/user/currentUser":
            self.reply({"code": 0, "data": {"loginName": "local-test"}} if self.server.logged_in else {"code": 401})
            return
        body = b"""<!doctype html><title>Local homepage</title><h1>Homepage login fixture</h1>
        <div id="login-state"></div><script>
        document.querySelector('#login-state').textContent=sessionStorage.getItem('token')?'signed-in':'sign-in';
        </script>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ==========================================
    # Function: Serve directory names and count any unintended inventory operations.
    # Method: Match the real static metadata endpoints and reject all other operations.
    # ==========================================
    def do_POST(self):
        from urllib.parse import parse_qs
        self.server.calls.append(self.path)
        values = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
        venues = directory_fixture()["venues"]
        if self.path == "/manage/venue/list":
            self.reply({"code": 0, "rows": [{"venueId": v["id"], "venueName": v["name"], "campusName": v["campus"]} for v in venues], "total": len(venues)})
        elif self.path == "/manage/venue/queryVenueById":
            venue = next(v for v in venues if v["id"] == values["id"][0])
            self.reply({"code": 0, "data": {"motionTypes": venue["sports"]}})
        else:
            self.reply({"code": 500, "msg": "unexpected operation"})

    # ==========================================
    # Function: Keep local test output focused on assertions.
    # Method: Suppress routine HTTP logs while retaining explicit request accounting.
    # ==========================================
    def log_message(self, *args):
        pass


# ==========================================
# Function: Verify real browser session operations without a live account or order.
# Method: Use a private local server and the production worker/client/cache code.
# ==========================================
@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "设置 RUN_BROWSER_TESTS=1 启用浏览器目录测试")
class DirectoryBrowserTests(unittest.TestCase):
    # ==========================================
    # Function: Start an isolated counted server and private account directory.
    # Method: Avoid shared authentication or project configuration files.
    # ==========================================
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), DirectoryHandler)
        self.server.calls, self.server.logged_in = [], True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/"
        self.config = native_config()
        save_config(self.config, self.root / "config.json")

    # ==========================================
    # Function: Release local browser-worker resources and counted server.
    # Method: Stop the HTTP loop before deleting private test files.
    # ==========================================
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    # ==========================================
    # Function: Fetch the complete catalog once without any inventory traffic.
    # Method: Execute real browser fetches and require a cached ensure to issue zero reads.
    # ==========================================
    def test_real_directory_requests_once_without_inventory(self):
        with open_browser(self.config, self.root / "auth") as (_, page):
            block_availability(page)
            page.goto(self.base)
            client = DirectoryClient(page)
            self.assertTrue(client.authenticated())
            cache = DirectoryCache(self.root / "auth")
            result = cache.ensure(client.fetch, lambda: None)
            self.assertEqual(result["venues"], directory_fixture()["venues"])
            calls = list(self.server.calls)
            cache.ensure(lambda *args: self.fail("cache hit fetched metadata"), lambda: self.fail("cache hit waited"))
            self.assertEqual(self.server.calls, calls)
            self.assertEqual(sum(path == "/manage/venue/list" for path in calls), 1)
            self.assertFalse(any("fieldDetail" in path or "ConfirmOrder" in path for path in calls))

    # ==========================================
    # Function: Save login while remaining on the homepage with an existing directory cache.
    # Method: Verify the production capture flow changes neither target settings nor route.
    # ==========================================
    def test_homepage_login_saves_without_booking_page(self):
        private_json(self.root / "auth/catalog.json", directory_fixture())
        controller = DesktopController(self.root / "config.json", self.root / "auth")
        original = controller.config_path.read_bytes()
        with open_browser(self.config, self.root / "auth") as (context, page):
            block_availability(page)
            page.goto(self.base)
            controller.capture_event.set()
            with patch("autoreserve.desktop_controller.HOME_URL", self.base):
                controller.homepage_login(context, page, DirectoryClient(page))
            self.assertEqual(page.url, self.base)
            self.assertEqual(controller.auth_status, "已登录")
        self.assertTrue((self.root / "auth/storage.json").exists())
        self.assertTrue((self.root / "auth/session.json").exists())
        self.assertEqual(controller.config_path.read_bytes(), original)
        self.assertFalse(any("/manage/venue/" in path or "fieldDetail" in path for path in self.server.calls))

    # ==========================================
    # Function: Block automatic slot calls even if a login page initiates them.
    # Method: Attempt fetch from the browser and prove it never reaches the server.
    # ==========================================
    def test_pre_monitor_inventory_is_blocked_before_transport(self):
        with open_browser(self.config, self.root / "auth") as (_, page):
            block_availability(page)
            page.goto(self.base)
            result = page.evaluate("""fetch('/manage/fieldDetail/queryFieldSituation',{method:'POST'}).then(()=>'sent').catch(()=>'blocked')""")
            self.assertEqual(result, "blocked")
            self.assertFalse(any("fieldDetail" in path for path in self.server.calls))

    # ==========================================
    # Function: Run real native-worker discovery once, then reuse its cache without a browser.
    # Method: Count paced metadata requests and fail any second browser launch.
    # ==========================================
    def test_native_worker_catalog_then_offline_cache_hit(self):
        private_json(self.root / "auth/session.json", {"origin": self.base.rstrip("/"), "values": {}})
        controller = DesktopController(self.root / "config.json", self.root / "auth")
        with patch("autoreserve.desktop_controller.HOME_URL", self.base):
            controller.start("catalog")
            controller.worker.join(timeout=20)
        self.assertFalse(controller.busy)
        self.assertTrue(controller.cache.path.exists())
        self.assertIsNone(controller.result)
        calls = list(self.server.calls)
        with patch("autoreserve.desktop_controller.open_browser", side_effect=AssertionError("browser on cache hit")):
            controller.start("catalog")
        self.assertEqual(self.server.calls, calls)
        self.assertFalse(any("fieldDetail" in path for path in calls))

    # ==========================================
    # Function: Stop expired authentication without updating the directory or session.
    # Method: Run the real catalog worker against code 401 and verify no retries or cache.
    # ==========================================
    def test_expired_session_is_reported_without_catalog_retry(self):
        self.server.logged_in = False
        private_json(self.root / "auth/session.json", {"origin": self.base.rstrip("/"), "values": {"mock": "keep"}})
        controller = DesktopController(self.root / "config.json", self.root / "auth")
        before = (self.root / "auth/session.json").read_bytes()
        with patch("autoreserve.desktop_controller.HOME_URL", self.base):
            controller.start("catalog")
            controller.worker.join(timeout=10)
        self.assertFalse(controller.busy)
        self.assertIn("失效", controller.auth_status)
        self.assertFalse(controller.cache.path.exists())
        self.assertEqual((self.root / "auth/session.json").read_bytes(), before)
        self.assertEqual(self.server.calls.count("/system/user/currentUser"), 1)
        self.assertFalse(any("/manage/venue/" in path or "fieldDetail" in path for path in self.server.calls))

    # ==========================================
    # Function: Seed the expired identity markers behind the real reported false login.
    # Method: Save fake cookies, localStorage, and sessionStorage without a live account.
    # ==========================================
    def seed_expired_login(self):
        with open_browser(self.config, self.root / "auth") as (context, page):
            page.goto(self.base)
            page.evaluate("""sessionStorage.setItem('token','expired-token');
                sessionStorage.setItem('isname','expired-user');
                localStorage.setItem('stale-user','expired-user');""")
            context.add_cookies([{"name": "stale-cookie", "value": "expired", "url": self.base}])
            save_session(context, page, self.root / "auth", self.base.rstrip("/"))
        self.server.logged_in = False

    # ==========================================
    # Function: Recover from false homepage login without restoring any expired browser data.
    # Method: Reproduce the stale badge, then open actual manual-login mode and reload.
    # ==========================================
    def test_explicit_login_starts_clean_and_stays_clean_on_reload(self):
        self.seed_expired_login()
        before = {name: (self.root / "auth" / name).read_bytes() for name in ("session.json", "storage.json")}
        with open_browser(self.config, self.root / "auth") as (_, page):
            page.goto(self.base)
            self.assertEqual(page.locator("#login-state").inner_text(), "signed-in")
            with self.assertRaises(LoginRequired):
                DirectoryClient(page).authenticated()
        with open_browser(self.config, self.root / "auth", login=True) as (context, page):
            block_availability(page)
            page.goto(self.base)
            self.assertEqual(page.locator("#login-state").inner_text(), "sign-in")
            self.assertEqual(page.evaluate("[sessionStorage.length,localStorage.length]"), [0, 0])
            self.assertFalse(any(c["name"] == "stale-cookie" for c in context.cookies()))
            page.reload()
            self.assertIsNone(page.evaluate("sessionStorage.getItem('token')"))
            self.assertEqual(page.locator("#login-state").inner_text(), "sign-in")
        for name, content in before.items():
            self.assertEqual((self.root / "auth" / name).read_bytes(), content)

    # ==========================================
    # Function: Persist fresh authentication only after the server confirms the replacement.
    # Method: Capture from manual login mode, then reopen in normal mode and verify new state.
    # ==========================================
    def test_confirmed_fresh_login_replaces_saved_session(self):
        self.seed_expired_login()
        private_json(self.root / "auth/catalog.json", directory_fixture())
        controller = DesktopController(self.root / "config.json", self.root / "auth")
        original_config = controller.config_path.read_bytes()
        with open_browser(self.config, self.root / "auth", login=True) as (context, page):
            block_availability(page)
            page.goto(self.base)
            page.evaluate("sessionStorage.setItem('token','new-token');localStorage.setItem('user','new-user')")
            context.add_cookies([{"name": "fresh-cookie", "value": "new", "url": self.base}])
            self.server.logged_in = True
            controller.capture_event.set()
            with patch("autoreserve.desktop_controller.HOME_URL", self.base):
                controller.homepage_login(context, page, DirectoryClient(page))
        with open_browser(self.config, self.root / "auth") as (context, page):
            page.goto(self.base)
            self.assertEqual(page.evaluate("sessionStorage.getItem('token')"), "new-token")
            self.assertEqual(page.evaluate("localStorage.getItem('user')"), "new-user")
            self.assertIsNone(page.evaluate("sessionStorage.getItem('isname')"))
            names = {c["name"] for c in context.cookies()}
            self.assertIn("fresh-cookie", names)
            self.assertNotIn("stale-cookie", names)
        self.assertEqual(controller.config_path.read_bytes(), original_config)
        self.assertFalse(any("fieldDetail" in path or "ConfirmOrder" in path for path in self.server.calls))

    # ==========================================
    # Function: Refuse to save a misleading frontend marker when the server still rejects it.
    # Method: Cancel after a failed capture and require the original session files unchanged.
    # ==========================================
    def test_unconfirmed_fresh_login_keeps_original_session(self):
        self.seed_expired_login()
        controller = DesktopController(self.root / "config.json", self.root / "auth")
        before = {name: (self.root / "auth" / name).read_bytes() for name in ("session.json", "storage.json")}
        original_log = controller.log

        # ==========================================
        # Function: End this mock login after its expected rejection.
        # Method: Signal cooperative stop when the failed capture is logged.
        # ==========================================
        def stop_after_rejection(message):
            original_log(message)
            if "登录已失效" in message:
                controller.stop_event.set()

        with open_browser(self.config, self.root / "auth", login=True) as (context, page):
            block_availability(page)
            page.goto(self.base)
            page.evaluate("sessionStorage.setItem('token','not-confirmed')")
            controller.capture_event.set()
            with patch("autoreserve.desktop_controller.HOME_URL", self.base), patch.object(controller, "log", stop_after_rejection):
                with self.assertRaises(InterruptedError):
                    controller.homepage_login(context, page, DirectoryClient(page))
        for name, content in before.items():
            self.assertEqual((self.root / "auth" / name).read_bytes(), content)
        self.assertFalse(controller.cache.path.exists())
