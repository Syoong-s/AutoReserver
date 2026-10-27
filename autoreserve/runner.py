"""Bounded polling and an irreversible, single-click submission phase."""

from dataclasses import replace
from datetime import datetime
import logging
from pathlib import Path
import time
from urllib.parse import urlsplit

from .config import parse_start
from .monitoring import AccessDenied, ReadDeferred, ReadPacer
from .sjtu import GridNotReady, SjtuPage, candidate_cells, expand_candidates
from .state import private_json

LOG = logging.getLogger(__name__)


# ==========================================
# Function: Preserve outcome evidence without causing a retry.
# Method: Always save result JSON; capture a screenshot on a best-effort basis.
# ==========================================
def evidence(page, directory, result):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_json(directory / "result.json", result)
    try:
        page.screenshot(path=str(directory / "page.png"), full_page=True, timeout=5000)
    except Exception as exc:
        LOG.warning("截图未保存：%s", type(exc).__name__)
    return result


# ==========================================
# Function: Wait for the configured absolute release time.
# Method: Compare timezone-aware timestamps and sleep in short intervals.
# ==========================================
def wait_for_start(config, stop=None):
    if config.start_at is None:
        return
    target = parse_start(config.start_at, config.timezone)
    remaining = target.timestamp() - time.time()
    if remaining > 0:
        LOG.info("已提前加载，等待 %s（剩余 %.1f 秒）", target.isoformat(), remaining)
    while (remaining := target.timestamp() - time.time()) > 0:
        if stop and stop():
            raise InterruptedError("用户已停止定时等待")
        time.sleep(min(remaining, 0.25))


# ==========================================
# Function: Interpret the site's order-confirmation response.
# Method: Require code zero and a scalar order ID, retaining ambiguity otherwise.
# ==========================================
def classify_response(payload):
    if not isinstance(payload, dict):
        return {"status": "unknown", "message": "响应不是 JSON 对象，请核对我的订单"}
    code = payload.get("code")
    # bool must not be accepted as numeric code zero.
    if type(code) not in (str, int):
        return {"status": "unknown", "message": "响应缺少有效 code，请核对我的订单"}
    if str(code) == "0":
        order_id = payload.get("data")
        if type(order_id) in (str, int) and str(order_id).strip():
            return {"status": "submitted", "order_id": str(order_id), "message": "订单已提交，未执行付款"}
        return {"status": "unknown", "message": "服务端返回成功但缺少订单 ID，请核对我的订单"}
    if str(code) == "1002":
        return {"status": "needs_verification", "message": "请在浏览器内手动完成安全验证"}
    return {"status": "rejected", "code": code, "message": str(payload.get("msg", "提交被拒绝"))}


# ==========================================
# Function: Submit exactly once and observe the site's result.
# Method: Arm the durable latch, click once, and monitor same-origin responses.
# ==========================================
def submit_once(page, button, config, target, journal, task_key=None):
    observed = []
    origin = urlsplit(config.booking_url)

    # ==========================================
    # Function: Observe only this site's native confirmation request.
    # Method: Filter origin, HTTP method, and exact endpoint suffix.
    # ==========================================
    def on_response(response):
        parsed = urlsplit(response.url)
        if parsed.netloc != origin.netloc or parsed.scheme != origin.scheme:
            return
        if not parsed.path.endswith("/venue/personal/ConfirmOrder") or response.request.method != "POST":
            return
        try:
            outcome = classify_response(response.json()) if response.ok else {
                "status": "unknown", "message": f"提交响应 HTTP {response.status}，请核对我的订单",
            }
        except Exception:
            outcome = {"status": "unknown", "message": "无法解析提交响应，请核对我的订单"}
        observed.append(outcome)

    page.on("response", on_response)
    journal.arm(config, target, task_key)
    key = task_key or config.task_key()
    try:
        button.click()
        deadline = time.monotonic() + config.result_timeout_seconds
        waiting_verification = False
        while time.monotonic() < deadline:
            while observed:
                outcome = observed.pop(0)
                journal.update(key, outcome["status"], outcome)
                if outcome["status"] == "needs_verification":
                    if config.headless:
                        return outcome
                    if not waiting_verification:
                        deadline = time.monotonic() + config.verification_timeout_seconds
                        waiting_verification = True
                        LOG.warning("出现安全验证，请在浏览器中手动完成；程序不再点击提交")
                else:
                    return outcome
            page.wait_for_timeout(100)
        outcome = {"status": "needs_verification" if waiting_verification else "unknown",
                   "message": "等待结果超时，禁止自动重试，请先核对网站订单"}
        journal.update(key, outcome["status"], outcome)
        return outcome
    except BaseException:
        # Even interrupted or failed clicks can have reached the server.
        journal.update(key, "unknown", {"message": "提交阶段中断，请先核对网站订单"})
        raise
    finally:
        page.remove_listener("response", on_response)


# ==========================================
# Function: Poll prioritized alternatives and produce at most one order.
# Method: Refresh serially within a monotonic time budget; never poll after submit.
# ==========================================
def run_reservation(page, config, journal, artifacts, submit=False, progress=None, stop=None, refresh=None, prepare_auth=None):
    if config.goals or config.repeat_rules:
        if config.repeat_rules or any(g.rush.enabled for g in config.goals) or any(t.context is not None for g in config.goals for t in g.candidates):
            from .multivenue import run_multivenue
            return run_multivenue(page, config, journal, artifacts, submit, progress, stop, ReadPacer(config), refresh, prepare_auth)
        return run_plan(page, config, journal, artifacts, submit, progress, stop)
    journal.ensure_new(config)
    site = SjtuPage(page, config)
    page.goto(config.booking_url, wait_until="domcontentloaded")
    site.ready()
    site.choose_sport()
    wait_for_start(config)
    if config.start_at is not None:
        page.reload(wait_until="domcontentloaded")
        site.ready()
        site.choose_sport()
    deadline = time.monotonic() + config.max_poll_seconds
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        LOG.info("第 %d 轮检查候选空位", attempt)
        for target in config.targets:
            if time.monotonic() >= deadline:
                break
            if not site.choose_date(target.date):
                continue
            if not site.select(target):
                continue
            LOG.info("已匹配 %s %s %s", target.date, target.court, ", ".join(target.slots))
            if not submit:
                return evidence(page, artifacts, {
                    "status": "preview", "message": "已验证选择，未点击立即下单或提交订单",
                    "date": target.date, "court": target.court, "slots": target.slots,
                })
            button = site.prepare_submit(target)
            result = submit_once(page, button, config, target, journal)
            result.update(date=target.date, court=target.court, slots=target.slots)
            return evidence(page, artifacts, result)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        page.wait_for_timeout(min(config.poll_interval_seconds, remaining) * 1000)
        if time.monotonic() >= deadline:
            break
        page.reload(wait_until="domcontentloaded")
        site.ready()
        site.choose_sport()
    return evidence(page, artifacts, {"status": "no_availability", "message": "监测时间内未找到符合配置的空位"})


# ==========================================
# Function: Monitor all unfinished goals using shared snapshots and a ready queue.
# Method: Read each unique date once per round, then submit available goals by priority.
# ==========================================
def run_plan(page, config, journal, artifacts, submit=False, progress=None, stop=None):
    from zoneinfo import ZoneInfo
    stop = stop or (lambda: False)
    pacer = ReadPacer(config)
    site = SjtuPage(page, config, stop)
    states = {}
    for goal in config.goals:
        record = journal.record(config.goal_key(goal))
        states[goal.id] = {
            "id": goal.id, "name": goal.name, "date": goal.candidates[0].date,
            "status": record["status"] if record else "pending",
            "detail": record["detail"] if record else {},
            "watch_state": "completed" if record and record["status"] == "submitted" else "monitoring",
            "last_checked_at": None, "check_count": 0,
        }

    # ==========================================
    # Function: Publish every goal's status and the shared next-access deadline.
    # Method: Include copies so the GUI can display all active watchers together.
    # ==========================================
    def publish(message, snapshot=None):
        if progress:
            progress({"message": message, "goals": [dict(s) for s in states.values()],
                      "snapshot": snapshot, "monitor": pacer.state()})

    # ==========================================
    # Function: Finish without discarding completed orders or pending watchers.
    # Method: Count only server-confirmed order IDs as successful goals.
    # ==========================================
    def finish(status, message):
        result = {"status": status, "message": message, "goals": list(states.values()),
                  "completed": sum(s["status"] == "submitted" for s in states.values()),
                  "total": len(states), "monitor": pacer.state()}
        publish(message)
        return evidence(page, artifacts, result)

    # ==========================================
    # Function: Explain global throttling while keeping the UI responsive.
    # Method: Publish the actual cooldown instead of creating per-goal timers.
    # ==========================================
    def pacing_progress(info):
        if info["wait_seconds"] > 0:
            message = info["reason"] if info["cooling_down"] else "全部目标持续监测中，等待下一次全局读取"
            publish(message)

    # ==========================================
    # Function: Apply one shared backoff to an unfinished read or DOM transition.
    # Method: Preserve server cooldowns and never reinterpret an armed submission.
    # ==========================================
    def defer_read(exc):
        pacer.check_denial()
        if pacer.server_error:
            reason, pacer.server_error = pacer.server_error, None
        elif isinstance(exc, ReadDeferred) and not isinstance(exc, GridNotReady):
            # ReadPacer already installed the Retry-After cooldown before raising.
            reason = str(exc)
        else:
            reason = str(exc) if isinstance(exc, ReadDeferred) else f"读取空位失败（{type(exc).__name__}）"
            pacer.defer(reason)
            reason = pacer.reason
        publish(reason)

    # ==========================================
    # Function: Execute one budgeted page or date read.
    # Method: Back off on transient read failures and reject throttled snapshots.
    # ==========================================
    def read(operation):
        if not pacer.wait(page, stop, pacing_progress):
            raise InterruptedError("用户停止监测")
        try:
            value = operation()
            pacer.check_response()
            pacer.healthy()
            return value
        except AccessDenied:
            raise
        except InterruptedError:
            raise
        except Exception as exc:
            defer_read(exc)
            return None

    # ==========================================
    # Function: Reload the booking page only when navigation or calendar expiry requires it.
    # Method: Charge one shared read budget for this native page transition.
    # ==========================================
    def load_booking():
        page.goto(config.booking_url, wait_until="domcontentloaded")
        site.ready()
        return site.choose_sport()

    # ==========================================
    # Function: Refresh one date for all goals sharing it.
    # Method: Click its native tab once, returning an explicit unopened-date marker.
    # ==========================================
    def read_date(value):
        if not site.choose_date(value):
            return {"date_not_open": True, "active_date": value}
        return site.loaded_snapshot(expected_date=value, expected_sport=config.sport)

    # ==========================================
    # Function: Select a goal's best currently available unclaimed alternative.
    # Method: Match against one shared date snapshot without further site requests.
    # ==========================================
    def best_candidate(goal, shot):
        state, conflicts = states[goal.id], 0
        candidates = expand_candidates(shot, goal.candidates)
        for target in candidates:
            candidate_config = replace(config.for_goal(goal), targets=[target])
            try:
                journal.ensure_new(candidate_config, config.goal_key(goal))
            except RuntimeError as exc:
                conflicts += 1
                state["detail"] = {"message": str(exc)}
                continue
            if candidate_cells(shot, target) is not None:
                return target
        if candidates and conflicts == len(candidates):
            state.update(status="conflict", watch_state="blocked",
                         detail={"message": "全部候选已有订单记录，请先核对"})
        return None

    if all(s["status"] == "submitted" for s in states.values()):
        return finish("completed", "全部目标已有成功订单，无需重复预约")
    page.on("response", pacer.observe)
    deadline = time.monotonic() + config.max_poll_seconds
    try:
        publish("所有未完成目标已加入监测，共用全局访问间隔")
        if submit and config.start_at:
            publish("等待设定的启动时间，尚未读取空位")
            wait_for_start(config, stop)
            deadline = time.monotonic() + config.max_poll_seconds
        seed = read(load_booking)
        needs_load = seed is None
        last_calendar = time.monotonic()
        while not stop():
            pending = []
            today = datetime.now(ZoneInfo(config.timezone)).date().isoformat()
            for goal in config.goals:
                state = states[goal.id]
                if state["status"] not in {"pending", "rejected", "preview"}:
                    continue
                if goal.candidates[0].date < today:
                    state.update(status="expired", watch_state="blocked", detail={"message": "目标日期已过"})
                    continue
                key = config.goal_key(goal)
                previous = journal.record(key)
                if submit and previous and previous["status"] == "rejected":
                    journal.archive_rejection(key)
                    state["status"] = "pending"
                pending.append(goal)
            if not pending:
                if all(s["status"] == "submitted" for s in states.values()):
                    return finish("completed", "全部预约目标已提交成功；未执行付款")
                return finish("needs_attention", "仍有目标需要人工核对，已保留全部进度")
            if needs_load or time.monotonic() - last_calendar >= config.calendar_refresh_seconds:
                seed = read(load_booking)
                if seed is None:
                    needs_load = True
                    if not submit or (not config.monitor_until_complete and time.monotonic() >= deadline):
                        return finish("pending", "读取失败，正在退避，目标尚未完成")
                    continue
                needs_load, last_calendar = False, time.monotonic()
            snapshots = {}
            dates = list(dict.fromkeys(g.candidates[0].date for g in pending))
            # One native availability fetch serves every candidate and goal of this date.
            for value in dates:
                if stop():
                    return finish("stopped", "监测已停止，成功进度已保留")
                for goal in pending:
                    if goal.candidates[0].date == value:
                        states[goal.id]["watch_state"] = "checking"
                if seed is not None and seed.get("active_date") == value:
                    shot, seed = seed, None
                else:
                    shot = read(lambda value=value: read_date(value))
                if shot is None:
                    needs_load = True
                    for goal in pending:
                        if goal.candidates[0].date == value:
                            states[goal.id]["watch_state"] = "backoff"
                    # Global rate limits apply to all goals, including those not yet scanned.
                    break
                snapshots[value] = shot
                checked = datetime.now(ZoneInfo(config.timezone)).isoformat()
                for goal in pending:
                    if goal.candidates[0].date == value:
                        state = states[goal.id]
                        state.update(watch_state="monitoring", last_checked_at=checked,
                                     check_count=state["check_count"] + 1)
                publish(f"日期 {value} 已更新，相关目标共享此次空位读取",
                        None if shot.get("date_not_open") else shot)
            ready = []
            for goal in pending:
                shot = snapshots.get(goal.candidates[0].date)
                if not shot or shot.get("date_not_open"):
                    continue
                target = best_candidate(goal, shot)
                if target:
                    states[goal.id]["watch_state"] = "available"
                    ready.append((goal, target))
            publish(f"本轮已检查 {len(snapshots)} 个日期，发现 {len(ready)} 个可预约目标")
            selected_dirty = False
            for goal, target in ready:
                if stop():
                    return finish("stopped", "监测已停止，成功进度已保留")
                state, key = states[goal.id], config.goal_key(goal)
                if needs_load or not page.locator("#apointmentDetails").count():
                    current = read(load_booking)
                    if current is None:
                        needs_load = True
                        break
                    needs_load, selected_dirty = False, False
                    last_calendar = time.monotonic()
                try:
                    current = site.loaded_snapshot(expected_sport=config.sport)
                    if current["active_date"] != target.date or selected_dirty:
                        current = read(lambda value=target.date: read_date(value))
                    if current is None:
                        needs_load = True
                        break
                    if current.get("date_not_open"):
                        continue
                    target = best_candidate(goal, current)
                    if target is None or not site.select(target):
                        state["watch_state"] = "monitoring"
                        continue
                    selected_dirty = True
                    candidate_config = replace(config.for_goal(goal), targets=[target])
                    if not submit:
                        state.update(status="preview", detail={"date": target.date, "court": target.court, "slots": target.slots})
                        publish("预览匹配成功，尚未提交订单", site.loaded_snapshot())
                        continue
                    if stop():
                        return finish("stopped", "已停止，没有提交当前目标")
                    pacer.check_denial()
                    button = site.prepare_submit(target)
                except GridNotReady as exc:
                    defer_read(exc)
                    state["watch_state"] = "backoff"
                    needs_load, seed = True, None
                    break
                if stop():
                    return finish("stopped", "已停止，没有提交当前目标")
                pacer.check_denial()
                publish(f"发现空位，正在下单：{goal.name or target.date}")
                result = submit_once(page, button, candidate_config, target, journal, key)
                state.update(status=result["status"], watch_state="completed" if result["status"] == "submitted" else "blocked",
                             detail=result | {"date": target.date, "court": target.court, "slots": target.slots})
                journal.update(key, result["status"], state["detail"] | {"goal_id": goal.id, "goal_name": goal.name})
                publish("订单已提交，其他未完成目标继续监测" if result["status"] == "submitted" else result["message"])
                needs_load, seed = True, None
            if not submit:
                return finish("preview", "所有目标预览结束，没有提交订单")
            if all(s["status"] == "submitted" for s in states.values()):
                return finish("completed", "所有预约目标均已提交成功，监测结束；未执行付款")
            if not any(s["status"] in {"pending", "rejected"} for s in states.values()):
                return finish("needs_attention", "尚有目标需要人工核对，已保留进度")
            if not config.monitor_until_complete and time.monotonic() >= deadline:
                return finish("pending", "本轮监测到时，尚有未完成目标")
            publish("等待空位，继续检查未完成目标")
            seed = None
        return finish("stopped", "已停止监测，成功目标的进度已保留")
    except AccessDenied as exc:
        return finish("access_limited", str(exc))
    except InterruptedError:
        return finish("stopped", "已停止监测，成功目标的进度已保留")
    finally:
        page.remove_listener("response", pacer.observe)
