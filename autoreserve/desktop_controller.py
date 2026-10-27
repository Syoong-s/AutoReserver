"""Native client workers: cached metadata, homepage login, and explicit monitoring."""

from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import queue
import threading
import time

from .browser import open_browser, origin_of, save_session
from .catalog import DirectoryCache, DirectoryClient, LoginRequired, block_availability, booking_url
from .config import BookingContext, HOME_URL, Target, load_config, save_config, parse_start
from .monitoring import GridReadStalled
from .gui import Controller
from .runner import evidence, run_reservation
from .state import Journal, account_lock, private_json


# ==========================================
# Function: Give each venue/project a separate resumable local plan.
# Method: Hash names rather than user-controlled filesystem path components.
# ==========================================
def context_key(venue, sport):
    return hashlib.sha256(json.dumps([venue, sport], ensure_ascii=False).encode()).hexdigest()


# ==========================================
# Function: Coordinate desktop actions without HTTP polling or browser UI rendering.
# Method: Reuse journal protections and send incremental events through a thread-safe queue.
# ==========================================
class DesktopController(Controller):
    # ==========================================
    # Function: Restore existing configuration, static catalog, and learned court names.
    # Method: Keep construction offline and preserve legacy journal migration.
    # ==========================================
    def __init__(self, config_path, auth_dir):
        self.events = queue.SimpleQueue()
        super().__init__(config_path, auth_dir)
        self.merge_workspace()
        journal = Journal(self.auth_dir / "journal.sqlite3")
        try:
            self._completed_seen = {(r["detail"].get("goal_id"), str(r["detail"].get("order_id")))
                                    for r in journal.entries() if r["status"] == "submitted"}
            config = load_config(self.config_path)
            for goal in config.goals:
                record = journal.record(config.goal_key(goal))
                if record and record["status"] == "submitted":
                    self._completed_seen.add((goal.id, str(record["detail"].get("order_id"))))
        finally:
            journal.close()
        self.cache = DirectoryCache(self.auth_dir)
        self.catalog = self.cache.load()
        self.auth_status = "已保存登录（启动时验证）" if (self.auth_dir / "session.json").exists() else "尚未登录"
        self.courts_path = self.auth_dir / "courts.json"
        try:
            self.courts = json.loads(self.courts_path.read_text(encoding="utf-8"))
            if not isinstance(self.courts, dict):
                self.courts = {}
        except (OSError, ValueError):
            self.courts = {}

    # ==========================================
    # Function: Merge previously separate project plans into one multi-venue workspace.
    # Method: Retain original goal scopes, back up settings, and preserve journal/slot identity.
    # ==========================================
    def merge_workspace(self):
        from dataclasses import asdict
        config = load_config(self.config_path)
        if config.workspace_version == 2:
            return
        backup = self.auth_dir / "workspace-v1.json"
        if not backup.exists():
            private_json(backup, asdict(config))
        sources = [config]
        for path in sorted((self.auth_dir / "plans").glob("*.json")):
            profile = load_config(path)
            if (profile.venue, profile.sport) != (config.venue, config.sport):
                sources.append(profile)
        merged, identities, seen_keys = [], set(), set()
        journal = Journal(self.auth_dir / "journal.sqlite3")
        try:
            for source in sources:
                if not source.venue:
                    continue
                context = BookingContext(source.booking_url, source.venue, source.sport)
                for goal in source.goals:
                    old_key = source.goal_key(goal)
                    if old_key in seen_keys:
                        continue
                    seen_keys.add(old_key)
                    identity = goal.id
                    if identity in identities:
                        identity = f"import-{old_key[:24]}"
                        suffix = 1
                        while identity in identities:
                            identity = f"import-{old_key[:24]}-{suffix}"
                            suffix += 1
                    identities.add(identity)
                    converted = replace(goal, id=identity, origin=goal.origin or context,
                                        candidates=[replace(t, context=t.context or context) for t in goal.candidates])
                    new_key = config.goal_key(converted)
                    journal.migrate_key(old_key, new_key)
                    record = journal.record(new_key)
                    if record:
                        detail = record["detail"]
                        if all(detail.get(k) for k in ("date", "court", "slots")):
                            target = Target(detail["date"], detail["court"], detail["slots"], context)
                            journal.add_slot_aliases(source, target, new_key)
                    merged.append(converted)
        finally:
            journal.close()
        config.goals, config.targets, config.workspace_version = merged, [], 2
        config.validate()
        save_config(config, self.config_path)

    # ==========================================
    # Function: Deliver an incremental UI event without touching native widgets.
    # Method: Leave all Tk operations on the main thread.
    # ==========================================
    def emit(self, kind, value=None):
        self.events.put((kind, value))
        wakeup = getattr(self, "event_wakeup", None)
        if wakeup:
            wakeup()

    # ==========================================
    # Function: Append only new log entries to the desktop activity panel.
    # Method: Reuse the bounded log buffer and queue the newest entry.
    # ==========================================
    def log(self, message):
        super().log(message)
        self.emit("log", self.logs[-1].copy())

    # ==========================================
    # Function: Remember exact court labels learned during active monitoring.
    # Method: Update a separate names cache only when names change, never refresh metadata.
    # ==========================================
    def progress(self, event):
        super().progress(event)
        shot = event.get("snapshot")
        if shot and shot.get("columns"):
            key = context_key(shot["venue"], shot["sport"])
            names = list(dict.fromkeys(shot["columns"]))
            if self.courts.get(key) != names:
                self.courts[key] = names
                private_json(self.courts_path, self.courts)
                self.emit("courts", names)
        for goal in event.get("goals", []):
            detail = goal.get("detail", {})
            order_id = detail.get("order_id")
            identity = (goal["id"], str(order_id))
            if goal.get("status") == "submitted" and order_id and identity not in self._completed_seen:
                self._completed_seen.add(identity)
                self.emit("goal_completed", {"id": goal["id"], "name": goal.get("name", "预约目标"),
                                              "detail": dict(detail)})
        self.emit("progress", event)

    # ==========================================
    # Function: Read cached court labels for the selected project without network traffic.
    # Method: Scope names to venue and sport, excluding stale cross-project UI choices.
    # ==========================================
    def court_names(self, config):
        return list(self.courts.get(context_key(config.venue, config.sport), []))

    # ==========================================
    # Function: Resolve a cached project for a candidate without switching the workspace.
    # Method: Return its booking defaults while preserving every goal and journal identity.
    # ==========================================
    def select_context(self, venue_id, sport_name):
        with self.lock:
            if self.busy:
                raise ValueError("请先停止当前操作，再切换场馆")
            catalog = self.cache.load()
            venues = [v for v in (catalog or {}).get("venues", []) if v["id"] == venue_id]
            if len(venues) != 1 or sport_name not in {s["name"] for s in venues[0]["sports"]}:
                raise ValueError("请选择缓存目录中的场馆和项目")
            venue = venues[0]
            return replace(load_config(self.config_path), booking_url=booking_url(venue_id, sport_name),
                           venue=venue["name"], sport=sport_name)

    # ==========================================
    # Function: Start an explicitly authorized login, one-time catalog read, or monitor.
    # Method: Keep cache hits entirely offline and serialize account/browser actions.
    # ==========================================
    def start(self, operation, date=None):
        if operation not in {"login", "catalog", "run"}:
            raise ValueError("客户端仅在启动监测后读取空位")
        with self.lock:
            if self.busy:
                raise ValueError("已有操作正在进行")
            if operation == "catalog":
                cached = self.cache.load()
                if cached is not None:
                    self.catalog = cached
                    self.emit("catalog", cached)
                    return
            config = load_config(self.config_path)
            if operation == "run":
                config.validate(require_targets=True)
            if operation != "login" and not (self.auth_dir / "session.json").exists():
                raise ValueError("请先在网站主界面登录，并点击保存登录")
            self.busy, self.operation, self.result = True, operation, None
            self.live_goals, self.monitor = [], {}
            if operation == 'run':
                self.snapshot = None
            self.stop_event.clear()
            self.capture_event.clear()
            self.emit("busy", operation)
            self.worker = threading.Thread(target=self.work, args=(operation, config, None), daemon=True)
            self.worker.start()

    # ==========================================
    # Function: Obtain the directory once with paced, cancelable metadata reads.
    # Method: Recheck the cache under the account lock and never invoke an inventory endpoint.
    # ==========================================
    def ensure_directory(self, page, client):
        last_read = [time.monotonic()]

        # ==========================================
        # Function: Space static metadata requests while processing stop events.
        # Method: Enforce at least two seconds between requests without blocking Tk.
        # ==========================================
        def wait():
            while time.monotonic() - last_read[0] < 2:
                if self.stop_event.is_set():
                    raise InterruptedError("目录读取已停止")
                page.wait_for_timeout(100)
            last_read[0] = time.monotonic()

        # ==========================================
        # Function: Preserve metadata pacing after an internally retried request completes.
        # Method: Restart the shared spacing clock after the full fetch instead of using the first attempt's timestamp.
        # ==========================================
        def fetch(endpoint, values):
            try:
                return client.fetch(endpoint, values)
            finally:
                last_read[0] = time.monotonic()

        self.catalog = self.cache.ensure(fetch, wait, self.log, self.stop_event.is_set)
        self.emit("catalog", self.catalog)
        self.log(f"目录已缓存：{len(self.catalog['venues'])} 个场馆；以后启动和切换均从本机读取")

    # ==========================================
    # Function: Save authentication from the homepage without requiring a booking page.
    # Method: Validate currentUser in a fresh context before replacing saved authentication.
    # ==========================================
    def homepage_login(self, context, page, client):
        self.log("已打开全新登录窗口，不载入旧登录标记；在网站完成登录后点击客户端“保存登录”")
        while not self.stop_event.is_set() and not page.is_closed():
            if self.capture_event.is_set():
                self.capture_event.clear()
                if origin_of(page.url) != origin_of(HOME_URL):
                    self.log("请完成 jAccount 登录并回到体育场馆网站，再点击保存登录")
                else:
                    try:
                        client.authenticated()
                    except LoginRequired as exc:
                        self.log(str(exc))
                    else:
                        save_session(context, page, self.auth_dir, origin_of(HOME_URL))
                        self.auth_status = "已登录"
                        self.emit("auth", self.auth_status)
                        self.log("登录已保存；场馆和项目由目录选择，不与当前网页绑定")
                        self.ensure_directory(page, client)
                        return
            page.wait_for_timeout(100)
        raise InterruptedError("登录已取消，原有登录和目标保留")

    # ==========================================
    # Function: Persist deterministic daily goals before attempting their bookings.
    # Method: Save on the browser worker and publish the new local workspace.
    # ==========================================
    def persist_daily(self, config):
        save_config(config, self.config_path)
        self.emit("workspace", asdict(config))

    # ==========================================
    # Function: Recover one stuck browser without replaying submitted or uncertain orders.
    # Method: Reopen only after read-only grid stalls, save the live session as on manual stop and retain the journal.
    # ==========================================
    def monitor_with_recovery(self, config, artifacts):
        deadline=None
        for restart in range(2):
            if self.stop_event.is_set():
                raise InterruptedError('监测已停止，不再重建浏览器')
            with open_browser(config,self.auth_dir) as (context,page):
                journal=None
                try:
                    page.goto(HOME_URL,wait_until='domcontentloaded')
                    client=DirectoryClient(page,stop=self.stop_event.is_set,progress=self.log)
                    client.authenticated()
                    self.auth_status='已登录'
                    self.emit('auth',self.auth_status)
                    current=config
                    timed=(config.max_poll_seconds>0 and not config.monitor_until_complete and not config.repeat_rules
                           and not any(goal.rush.enabled for goal in config.goals))
                    if deadline is None and timed:
                        delay=max(0,parse_start(config.start_at,config.timezone).timestamp()-time.time()) if config.start_at else 0
                        deadline=time.monotonic()+delay+config.max_poll_seconds
                    elif deadline is not None:
                        remaining=deadline-time.monotonic()
                        if remaining<=0:
                            self.result={'status':'pending','message':'监测时长已到，未继续重建后的读取'}
                            self.log(self.result['message'])
                            return
                        current=replace(config,max_poll_seconds=remaining)
                    journal=Journal(self.auth_dir/'journal.sqlite3')
                    self.result=run_reservation(page,current,journal,artifacts,True,self.progress,
                                                self.stop_event.is_set,self.persist_daily,prepare_auth=client.authenticated)
                    self.log(self.result['message'])
                    if not page.is_closed() and origin_of(page.url)==origin_of(HOME_URL):
                        save_session(context,page,self.auth_dir,origin_of(HOME_URL))
                    return
                except GridReadStalled as exc:
                    evidence(page,Path(artifacts)/f'read-recovery-{restart+1}',{'status':'read_stalled','message':str(exc)})
                    if restart:
                        raise RuntimeError('重建浏览器后仍连续读取超时，已停止自动重试；请检查网络、登录和网站状态。'+str(exc)) from exc
                    if self.stop_event.is_set():
                        raise InterruptedError('已停止，不再重建浏览器') from exc
                    if not page.is_closed() and origin_of(page.url)==origin_of(HOME_URL):
                        save_session(context,page,self.auth_dir,origin_of(HOME_URL))
                    self.log('连续网格读取超时，正在重建一次监测浏览器；登录和已有订单进度保留，不重复提交已完成订单')
                except Exception as exc:
                    if not isinstance(exc,InterruptedError):
                        evidence(page,artifacts,{'status':'error','message':str(exc)})
                    raise
                finally:
                    if journal:
                        journal.close()

    # ==========================================
    # Function: Run authenticated browser work without touching native widgets.
    # Method: Persist daily occurrences on this worker before monitoring their shared inventory.
    # ==========================================
    def work(self, operation, config, date=None):
        artifacts = self.config_path.parent / "artifacts" / datetime.now().strftime("native-%Y%m%d-%H%M%S-%f")
        try:
            with account_lock(self.auth_dir):
                if operation=='run':
                    self.monitor_with_recovery(config,artifacts)
                    return
                if operation == "catalog" and self.cache.load() is not None:
                    self.catalog = self.cache.load()
                    self.emit("catalog", self.catalog)
                    return
                browser_config = replace(config, headless=True) if operation == "catalog" else config
                with open_browser(browser_config, self.auth_dir, login=operation == "login") as (context, page):
                    try:
                        if operation != "run":
                            block_availability(page)
                        page.goto(HOME_URL, wait_until="domcontentloaded")
                        client = DirectoryClient(page, stop=self.stop_event.is_set, progress=self.log)
                        if operation == "login":
                            self.homepage_login(context, page, client)
                        else:
                            client.authenticated()
                            self.auth_status = "已登录"
                            self.emit("auth", self.auth_status)
                            self.ensure_directory(page, client)
                            if not page.is_closed() and origin_of(page.url) == origin_of(HOME_URL):
                                save_session(context, page, self.auth_dir, origin_of(HOME_URL))
                    except Exception as exc:
                        if not isinstance(exc, InterruptedError):
                            evidence(page, artifacts, {"status": "error", "message": str(exc)})
                        raise
        except InterruptedError as exc:
            self.log(str(exc))
        except Exception as exc:
            if isinstance(exc, LoginRequired):
                self.auth_status = "登录已失效，请重新登录"
                self.emit("auth", self.auth_status)
            self.result = {"status": "error", "message": str(exc)}
            self.log(f"需要处理：{exc}")
        finally:
            with self.lock:
                self.busy, self.operation = False, "idle"
            try:
                self.emit("states", self.state()["goals"])
            except Exception as exc:
                self.log(f"本地进度读取失败：{exc}")
            finally:
                self.emit("finished", self.result)
