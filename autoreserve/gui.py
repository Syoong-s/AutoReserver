"""Local graphical console and worker ownership for Playwright's synchronous API."""

from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit, unquote

from .browser import open_browser, origin_of, require_playwright, save_session
from .config import Config, Goal, config_from_dict, example_config, load_config, save_config
from .runner import evidence, run_reservation
from .sjtu import SjtuPage
from .state import Journal, account_lock

ASSETS = Path(__file__).parent / "ui"


# ==========================================
# Function: Own GUI operations and expose bounded, authentication-free status data.
# Method: Run each browser operation in one worker thread, with cooperative stop.
# ==========================================
class Controller:
    # ==========================================
    # Function: Initialize local configuration and operation signals.
    # Method: Preserve existing config while migrating legacy alternatives to one goal.
    # ==========================================
    def __init__(self, config_path, auth_dir):
        self.config_path = Path(config_path)
        self.auth_dir = Path(auth_dir)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.capture_event = threading.Event()
        self.worker = None
        self.busy = False
        self.operation = "idle"
        self.message = "先登录并选择场馆，再设置预约目标"
        self.logs = []
        self.snapshot = None
        self.result = None
        self.live_goals = []
        self.monitor = {}
        self.auth_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.config_path.exists():
            save_config(example_config(), self.config_path)
        config = load_config(self.config_path)
        if not config.goals and config.targets:
            previous_key = config.task_key()
            config.goals = [Goal("legacy-" + previous_key[:16], "预约目标 1", config.targets)]
            config.targets = []
            config.monitor_until_complete = True
            journal = Journal(self.auth_dir / "journal.sqlite3")
            try:
                journal.migrate_key(previous_key, config.goal_key(config.goals[0]))
            finally:
                journal.close()
            save_config(config, self.config_path)
        stored = json.loads(self.config_path.read_text(encoding="utf-8"))
        if stored.get("poll_interval_seconds") != config.poll_interval_seconds or "poll_jitter_ratio" not in stored:
            save_config(config, self.config_path)

    # ==========================================
    # Function: Append a concise human-readable activity entry.
    # Method: Retain only the latest 200 timestamped messages.
    # ==========================================
    def log(self, message):
        with self.lock:
            self.message = message
            self.logs.append({"time": datetime.now().astimezone().isoformat(), "message": message})
            self.logs = self.logs[-200:]

    # ==========================================
    # Function: Receive progress without sharing a SQLite connection across threads.
    # Method: Copy owned progress under a lock and keep transient loading/countdown events out of the activity log.
    # ==========================================
    def progress(self, event):
        with self.lock:
            self.live_goals = event.get("goals", self.live_goals)
            if event.get("monitor") is not None:
                self.monitor = event["monitor"]
            if event.get("snapshot") is not None:
                self.snapshot = event["snapshot"]
            if event.get("message") != self.message:
                if event.get('log',True):
                    self.log(event["message"])
                else:
                    self.message=event['message']

    # ==========================================
    # Function: Return configuration, login readiness, and per-goal progress.
    # Method: Read only public status fields and durable order records.
    # ==========================================
    def state(self):
        with self.lock:
            config = load_config(self.config_path)
            journal = Journal(self.auth_dir / "journal.sqlite3")
            try:
                live = {g["id"]: g for g in self.live_goals}
                goals = []
                for goal in config.goals:
                    record = journal.record(config.goal_key(goal))
                    status = record["status"] if record else "pending"
                    detail = record["detail"] if record else {}
                    if not record and goal.id in live:
                        status, detail = live[goal.id]["status"], live[goal.id]["detail"]
                    entry = {"id": goal.id, "status": status, "detail": detail}
                    if goal.id in live:
                        for name in ("watch_state", "last_checked_at", "check_count"):
                            entry[name] = live[goal.id].get(name)
                    goals.append(entry)
            finally:
                journal.close()
            return {"config": asdict(config), "busy": self.busy, "operation": self.operation,
                    "message": self.message, "logs": list(self.logs), "snapshot": self.snapshot,
                    "goals": goals, "result": self.result,
                    "monitor": dict(self.monitor),
                    "logged_in": (self.auth_dir / "storage.json").exists() and (self.auth_dir / "session.json").exists(),
                    "config_version": self.config_path.stat().st_mtime_ns}

    # ==========================================
    # Function: Save UI preferences without invalidating successful orders.
    # Method: Keep durable goal identity stable while allowing stopped requirements to change without rewriting order history.
    # ==========================================
    def save(self, data, *, allow_busy=False, notify=True):
        with self.lock:
            if self.busy and not allow_busy:
                raise ValueError("请先停止当前操作，再修改配置")
            config = config_from_dict(data)
            old = load_config(self.config_path)
            old_goals = {g.id: g for g in old.goals}
            journal = Journal(self.auth_dir / "journal.sqlite3")
            try:
                for goal in config.goals:
                    previous = old_goals.get(goal.id)
                    record = journal.record(old.goal_key(previous)) if previous else None
                    if record:
                        if config.goal_key(goal) != old.goal_key(previous):
                            raise ValueError("已有订单目标的身份不能修改，请添加新目标")
            finally:
                journal.close()
            save_config(config, self.config_path)
            if not allow_busy:
                self.live_goals = []
                self.monitor = {}
            if notify:
                self.log("预约目标和优先级已保存")

    # ==========================================
    # Function: Start one browser action without blocking HTTP requests.
    # Method: Set ownership before spawning the worker, rejecting concurrent actions.
    # ==========================================
    def start(self, operation, date=None):
        if operation not in {"login", "scan", "preview", "run"}:
            raise ValueError("未知操作")
        with self.lock:
            if self.busy:
                raise ValueError("已有操作正在进行")
            config = load_config(self.config_path)
            if operation in {"run", "preview"}:
                config.validate(require_targets=True)
            if operation != "login" and not (self.auth_dir / "session.json").exists():
                raise ValueError("请先点击登录，并保存登录/场馆")
            self.busy, self.operation, self.result = True, operation, None
            self.live_goals = []
            self.monitor = {}
            self.stop_event.clear()
            self.capture_event.clear()
            self.worker = threading.Thread(target=self.work, args=(operation, config, date), daemon=True)
            self.worker.start()

    # ==========================================
    # Function: Signal a graceful stop without racing a submission response.
    # Method: Let the owning browser thread finish its current atomic operation.
    # ==========================================
    def stop(self):
        self.stop_event.set()
        self.log("正在停止；若已点击提交，将先等待本次结果")

    # ==========================================
    # Function: Capture login when the user presses the GUI button.
    # Method: Keep browser access inside the login worker thread.
    # ==========================================
    def capture(self):
        with self.lock:
            if not self.busy or self.operation != "login":
                raise ValueError("请先打开登录浏览器")
            self.capture_event.set()

    # ==========================================
    # Function: Perform browser login, scan, or a full multi-goal run.
    # Method: Hold the account lock and preserve result evidence on failures.
    # ==========================================
    def work(self, operation, config, date):
        artifacts = self.config_path.parent / "artifacts" / datetime.now().strftime("gui-%Y%m%d-%H%M%S-%f")
        artifacts.mkdir(parents=True, mode=0o700)
        try:
            with account_lock(self.auth_dir), open_browser(config, self.auth_dir, login=operation == "login") as (context, page):
                try:
                    if operation == "login":
                        self.login_worker(context, page, config)
                    elif operation == "scan":
                        site = SjtuPage(page, config)
                        page.goto(config.booking_url, wait_until="domcontentloaded")
                        site.ready()
                        site.choose_sport()
                        if date and not site.choose_date(date):
                            raise ValueError("该日期尚未开放，请选择网站日期范围内的日期")
                        with self.lock:
                            self.snapshot = site.loaded_snapshot(expected_sport=config.sport)
                        self.log("已读取当前空位，可在目标中批量选择时段和场地")
                    else:
                        journal = Journal(self.auth_dir / "journal.sqlite3")
                        try:
                            result = run_reservation(page, config, journal, artifacts,
                                                     operation == "run", self.progress, self.stop_event.is_set)
                            with self.lock:
                                self.result = result
                            self.log(result["message"])
                        finally:
                            journal.close()
                except Exception as exc:
                    evidence(page, artifacts, {"status": "error", "message": str(exc)})
                    raise
                finally:
                    if not page.is_closed() and origin_of(page.url) == origin_of(config.booking_url):
                        try:
                            save_session(context, page, self.auth_dir, origin_of(config.booking_url))
                        except Exception:
                            logging.getLogger(__name__).warning("未能刷新登录状态")
        except Exception as exc:
            with self.lock:
                self.result = {"status": "error", "message": str(exc)}
            self.log(f"需要处理：{exc}")
        finally:
            with self.lock:
                self.busy, self.operation = False, "idle"

    # ==========================================
    # Function: Wait for manual login and save the chosen personal booking page.
    # Method: Respond to GUI capture signals while pumping browser events.
    # ==========================================
    def login_worker(self, context, page, config):
        self.log("在新浏览器中登录，进入具体场馆与项目后，点击本界面的“保存登录/场馆”")
        page.goto(config.booking_url, wait_until="domcontentloaded")
        while not self.stop_event.is_set() and not page.is_closed():
            if self.capture_event.is_set():
                self.capture_event.clear()
                parsed = urlsplit(page.url)
                shot = SjtuPage(page, config).snapshot()
                if origin_of(page.url) != "https://sports.sjtu.edu.cn" or not unquote(parsed.fragment).startswith("/apointmentDetails/1/") or not shot or not shot["venue"] or not shot["sport"]:
                    self.log("当前还不是个人预约页，请打开具体场馆，看到日期和场地网格后再保存")
                else:
                    new_context = (page.url, shot["venue"], shot["sport"])
                    old_context = (config.booking_url, config.venue, config.sport)
                    journal = Journal(self.auth_dir / "journal.sqlite3")
                    try:
                        has_records = any(journal.record(config.goal_key(g)) for g in config.goals)
                    finally:
                        journal.close()
                    if has_records and new_context != old_context:
                        self.log("已有订单目标属于原场馆，请回到原场馆保存；其他场馆请使用独立配置")
                        page.wait_for_timeout(150)
                        continue
                    config.booking_url, config.venue, config.sport = page.url, shot["venue"], shot["sport"]
                    config.validate()
                    save_session(context, page, self.auth_dir, origin_of(page.url))
                    with self.lock:
                        save_config(config, self.config_path)
                        self.snapshot = shot
                    self.log(f"登录已保存：{config.venue} / {config.sport}")
                    return
            page.wait_for_timeout(150)
        self.log("登录窗口已关闭，未更新场馆配置")


# ==========================================
# Function: Serve an authenticated local control surface.
# Method: Restrict hosts, origins, static paths, JSON size, and mutating request tokens.
# ==========================================
class GuiHandler(BaseHTTPRequestHandler):
    # ==========================================
    # Function: Enforce localhost request identity and same-origin API calls.
    # Method: Validate Host/Origin and a random token embedded in the local page.
    # ==========================================
    def authorized(self, api=False):
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in allowed:
            self.send_error(403)
            return False
        if self.headers.get("Origin") not in {None, f"http://{host}"}:
            self.send_error(403)
            return False
        if api and not secrets.compare_digest(self.headers.get("X-Reserve-Token", ""), self.server.token):
            self.send_error(403)
            return False
        return True

    # ==========================================
    # Function: Return a structured response without caching account data.
    # Method: Encode UTF-8 JSON and set explicit content length.
    # ==========================================
    def json_response(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ==========================================
    # Function: Serve the dashboard and sanitized state.
    # Method: Use an explicit static-file allowlist rather than filesystem browsing.
    # ==========================================
    def do_GET(self):
        if not self.authorized(self.path.startswith("/api/")):
            return
        if self.path == "/api/state":
            self.json_response(self.server.controller.state())
            return
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
        if self.path not in assets:
            self.send_error(404)
            return
        name, content_type = assets[self.path]
        body = (ASSETS / name).read_bytes()
        if name == "index.html":
            body = body.replace(b"__RESERVE_TOKEN__", self.server.token.encode())
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    # ==========================================
    # Function: Dispatch a user-authorized dashboard action.
    # Method: Accept bounded JSON and keep Playwright work asynchronous.
    # ==========================================
    def do_POST(self):
        if not self.authorized(api=True):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 256000 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("请求必须是有效的 JSON")
            data = json.loads(self.rfile.read(length))
            controller = self.server.controller
            if self.path == "/api/config":
                controller.save(data)
            elif self.path == "/api/action":
                action = data.get("action")
                if action == "stop":
                    controller.stop()
                elif action == "capture":
                    controller.capture()
                else:
                    controller.start(action, data.get("date"))
            else:
                self.send_error(404)
                return
            self.json_response({"ok": True})
        except (ValueError, TypeError, RuntimeError, KeyError) as exc:
            self.json_response({"error": str(exc)}, 400)

    # ==========================================
    # Function: Avoid printing every one-second status poll to the terminal.
    # Method: Silence the HTTP server's routine access log.
    # ==========================================
    def log_message(self, format, *args):
        pass


# ==========================================
# Function: Construct a loopback dashboard server for production and tests.
# Method: Assign a per-process token and attach the operation controller.
# ==========================================
def make_server(config_path, auth_dir, port=8766):
    server = ThreadingHTTPServer(("127.0.0.1", port), GuiHandler)
    server.daemon_threads = True
    server.token = secrets.token_urlsafe(32)
    server.controller = Controller(config_path, auth_dir)
    return server


# ==========================================
# Function: Start a single-window graphical application.
# Method: Serve locally and open the dashboard in a separate visible Chromium window.
# ==========================================
def serve_gui(config_path, auth_dir, port=8766, no_open=False):
    if not no_open:
        require_playwright()
    server = make_server(config_path, auth_dir, port)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"预约助手界面：{url}", flush=True)
    if no_open:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.controller.stop_event.set()
            server.server_close()
        return 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with open_browser(Config(headless=False), Path(auth_dir) / "gui") as (_, page):
            page.goto(url)
            while not page.is_closed():
                try:
                    page.wait_for_timeout(200)
                except Exception:
                    if page.is_closed():
                        break
                    raise
    except KeyboardInterrupt:
        pass
    finally:
        server.controller.stop_event.set()
        if server.controller.worker:
            server.controller.worker.join(timeout=35)
        server.shutdown()
        server.server_close()
    return 0
