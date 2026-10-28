"""Prioritized cross-venue goals sharing one browser and one account read budget."""

from dataclasses import replace
from datetime import datetime
import time
from zoneinfo import ZoneInfo

from .monitoring import AccessDenied, ReadDeferred
from .catalog import LoginRequired
from .browser import navigate_booking
from .repeat import materialize_daily, next_daily
from .runner import attention_message, evidence, submit_once, wait_for_start
from .sjtu import GridNotReady, SjtuPage, candidate_cells, expand_candidates
from .rush import RushBoundary, RushCoordinator


# ==========================================
# Function: Identify one effective venue/project without changing goal identity.
# Method: Include exact route and labels so snapshots can never cross contexts.
# ==========================================
def target_context(config, target):
    effective = config.for_target(target)
    return effective.booking_url, effective.venue, effective.sport


# ==========================================
# Function: Monitor all dates and alternative venues before choosing one order per goal.
# Method: Freshly load hash-routed venues, isolate failed reads and share scoped snapshots under one account limiter.
# ==========================================
def run_multivenue(page, config, journal, artifacts, submit, progress, stop, pacer, refresh=None, prepare_auth=None):
    stop = stop or (lambda: False)
    states, daily_wait_message, checked_minute = {}, None, None
    has_rush = any(g.rush.enabled for g in config.goals) or any(r.enabled and r.rush.enabled for r in config.repeat_rules)
    timing = RushCoordinator(config, pacer)
    cycle_released = set()
    cycle_preparing = set()
    preparing_now = False
    last_auth_check = None
    last_read_error = None
    guard_seconds = max(2, min(10, config.action_timeout_ms / 1000))

    # ==========================================
    # Function: Add due daily goals alongside every unfinished existing date.
    # Method: Materialize offline, persist before reads, and restore durable statuses for new IDs.
    # ==========================================
    def sync_daily():
        nonlocal checked_minute
        now = datetime.now(ZoneInfo(config.timezone))
        minute = now.strftime("%Y-%m-%d %H:%M:%S" if has_rush else "%Y-%m-%d %H:%M")
        changed = False
        if submit and config.repeat_rules and minute != checked_minute:
            changed = materialize_daily(config, now=now, records=lambda g: journal.record(config.goal_key(g)))
            checked_minute = minute
        if changed and refresh:
            refresh(config)
        identities = {g.id for g in config.goals}
        for identity in list(states):
            if identity not in identities:
                del states[identity]
        for goal in config.goals:
            if goal.id in states:
                continue
            record = journal.record(config.goal_key(goal))
            status = record["status"] if record else "pending"
            states[goal.id] = {"id": goal.id, "name": goal.name, "date": goal.candidates[0].date,
                               "status": status, "detail": record["detail"] if record else {},
                               "watch_state": "completed" if status == "submitted" else "monitoring",
                               "last_checked_at": None, "check_count": 0}

    sync_daily()
    recurring = submit and any(r.enabled for r in config.repeat_rules)
    current_context, site, effective = None, None, None
    needs_load, selected_dirty, seed = True, False, None
    last_calendar = 0
    unopened_dates = set()
    deadline = config.monitor_deadline(time.monotonic())

    # ==========================================
    # Function: Publish independent goal progress without mutating UI objects.
    # Method: Include lightweight copies and an optional confirmed completion event.
    # ==========================================
    def publish(message, snapshot=None, completed=None):
        if has_rush:
            for goal in config.goals:
                if goal.id in states and goal.rush.enabled:
                    states[goal.id]['rush'] = timing.state(goal)
        if progress:
            progress({"message": message, "goals": [dict(s) for s in states.values()],
                      "snapshot": snapshot, "monitor": pacer.state(), "completed_goal": completed})

    # ==========================================
    # Function: Preserve partial progress for every normal or interrupted exit.
    # Method: Count only confirmed submitted orders as completed goals.
    # ==========================================
    def finish(status, message):
        result = {"status": status, "message": message, "goals": list(states.values()),
                  "completed": sum(s["status"] == "submitted" for s in states.values()),
                  "total": len(states), "monitor": pacer.state()}
        publish(message)
        return evidence(page, artifacts, result)

    # ==========================================
    # Function: Back off globally before another venue or date is read.
    # Method: Preserve Retry-After and distinguish denial from recoverable loading.
    # ==========================================
    def defer(exc):
        pacer.check_denial()
        if pacer.server_error:
            reason, pacer.server_error = pacer.server_error, None
        elif isinstance(exc, ReadDeferred) and not isinstance(exc, GridNotReady):
            reason = str(exc)
        else:
            if isinstance(exc,GridNotReady) or type(exc).__name__=='TimeoutError':
                pacer.grid_failed(str(exc) if isinstance(exc,GridNotReady) else f'读取空位失败（{type(exc).__name__}）')
            pacer.defer(str(exc) if isinstance(exc, GridNotReady) else f"读取空位失败（{type(exc).__name__}）")
            reason = pacer.reason
        publish(reason)

    # ==========================================
    # Function: Execute exactly one network-producing monitor operation.
    # Method: Pace all contexts together and distinguish account-wide throttling from venue-local read failures.
    # ==========================================
    def read(operation):
        nonlocal last_read_error
        last_read_error=None
        wake = {'wake': boundary} if has_rush and submit else {}
        if not pacer.wait(page, stop, lambda info: publish(info["reason"] or "所有场馆共同监测，等待全局读取间隔"), **wake):
            if not stop() and has_rush and boundary():
                raise RushBoundary()
            raise InterruptedError("用户停止监测")
        try:
            shot = operation()
            pacer.check_response()
            pacer.healthy(complete_cycle=False)
            return shot
        except (AccessDenied,LoginRequired,InterruptedError):
            raise
        except Exception as exc:
            last_read_error=exc
            defer(exc)
            return None

    # ==========================================
    # Function: Yield a normal/preparation batch exactly when an unhandled rush deadline arrives.
    # Method: Compare live monotonic eligibility before another budget lease is consumed.
    # ==========================================
    def boundary():
        goals = [g for g in config.goals if states.get(g.id, {}).get('status') in {'pending', 'rejected', 'preview'}]
        return (any(g.rush.enabled and g.id not in cycle_released and timing.released(g) for g in goals)
                or any(g.id not in cycle_preparing for g in timing.preparing(goals))
                or (not preparing_now and not any(g.rush.enabled and timing.released(g) for g in goals) and bool(reserved(goals))))

    # ==========================================
    # Function: Preserve the prewarmed page immediately before a timed release.
    # Method: Yield ordinary work during the final action-timeout guard instead of starting a late navigation.
    # ==========================================
    def reserved(goals):
        return [g for g in goals if g.rush.enabled and g.id in timing.prepared
                and 0 < timing.deadline(g) - pacer.clock() <= guard_seconds]

    # ==========================================
    # Function: Load a venue/project only when switching or recovering requires it.
    # Method: Replace scoped observers and force a fresh document when venue routes reuse the same hash-based application.
    # ==========================================
    def load(context):
        nonlocal current_context, site, effective, selected_dirty, last_calendar
        if site is not None:
            site.close()
        effective = replace(config, booking_url=context[0], venue=context[1], sport=context[2], goals=[])
        site = SjtuPage(page, effective, stop)
        current_context = context
        last_calendar=time.monotonic()
        navigate_booking(page,effective.booking_url)
        site.ready()
        shot = site.choose_sport()
        selected_dirty, last_calendar = False, time.monotonic()
        return shot

    # ==========================================
    # Function: Refresh a date after the correct venue/project is active.
    # Method: Click the native date tab and return a settled, scoped snapshot.
    # ==========================================
    def date_read(day):
        nonlocal selected_dirty
        if not site.choose_date(day):
            return {"date_not_open": True, "active_date": day}
        selected_dirty = False
        return site.loaded_snapshot(expected_date=day, expected_sport=effective.sport)

    # ==========================================
    # Function: Choose the highest-priority unclaimed available alternative for a goal.
    # Method: Match each candidate only against its own context/date snapshot.
    # ==========================================
    def best(goal, snapshots, restricted=None):
        expanded, conflicts, unresolved = 0, 0, False
        conflict_message = None
        for candidate in goal.candidates:
            context = target_context(config, candidate)
            if restricted is not None and context != restricted:
                continue
            shot = snapshots.get((context, candidate.date))
            if not shot or shot.get("date_not_open"):
                unresolved = True
                if goal.rush.enabled and not shot:
                    return None
                continue
            targets = expand_candidates(shot, [candidate])
            if not targets:
                unresolved = True
            for target in targets:
                expanded += 1
                try:
                    journal.ensure_new(config.for_target(target), config.goal_key(goal))
                except RuntimeError as error:
                    conflicts += 1
                    conflict_message = str(error)
                    continue
                if candidate_cells(shot, target) is not None:
                    return target
        if restricted is None and not unresolved and expanded and conflicts == expanded:
            states[goal.id].update(status="conflict", watch_state="blocked", detail={"message": conflict_message or '请在左栏「历史订单」查看该时段的提交记录'})
        return None

    if not recurring and all(s["status"] == "submitted" for s in states.values()):
        return finish("completed", "全部目标已有成功订单，无需重复预约")
    page.on("response", pacer.observe)
    try:
        publish("所有目标与场馆已加入共享监测")
        if submit and config.start_at and not has_rush:
            publish("等待设定的启动时间，尚未读取空位")
            wait_for_start(config, stop)
            deadline = config.monitor_deadline(time.monotonic())
        while not stop():
            try:
                sync_daily()
                cycle_released = {g.id for g in config.goals if g.rush.enabled and timing.released(g)}
                cycle_preparing = {g.id for g in timing.preparing(config.goals)}
                today = datetime.now(ZoneInfo(config.timezone)).date().isoformat()
                pending = []
                for goal in config.goals:
                    state = states[goal.id]
                    if state["status"] not in {"pending", "rejected", "preview"}:
                        continue
                    if goal.candidates[0].date < today:
                        state.update(status="expired", watch_state="blocked")
                        continue
                    if submit and state["status"] == "rejected":
                        journal.archive_rejection(config.goal_key(goal))
                        state["status"] = "pending"
                    pending.append(goal)
                if submit and has_rush:
                    timing.update(pending)
                    if pending:
                        deadline = max([deadline, *[timing.deadline(g) + config.max_poll_seconds for g in pending if g.rush.enabled]])
                    preparation = timing.preparing(pending)
                    if preparation:
                        preparing_now = True
                        publish('抢购准备：校时、验证登录并提前进入优先场馆')
                        if prepare_auth and (last_auth_check is None or pacer.clock() - last_auth_check > 300):
                            prepare_auth()
                            last_auth_check = pacer.clock()
                        publish(timing.calibrate(page, preparation, stop))
                        timing.update(pending)
                        for goal in timing.preparing(pending):
                            context, day = target_context(config, goal.candidates[0]), goal.candidates[0].date
                            if current_context != context or needs_load:
                                warm = read(lambda context=context: load(context))
                                needs_load = warm is None
                            else:
                                warm = site.loaded_snapshot(expected_sport=effective.sport)
                            if warm is not None and warm.get('active_date') != day:
                                warm = read(lambda day=day: date_read(day))
                            if warm is not None:
                                timing.prepared[goal.id] = {'date_open': not warm.get('date_not_open')}
                                publish('优先场馆已预热，等待抢购时刻；尚未选择或提交')
                            seed = None  # Stock from preparation must never authorize a release-time order.
                        preparing_now = False
                    waiting = pending
                    pending = [g for g in waiting if timing.released(g)]
                    if reserved(waiting) and not any(g.rush.enabled for g in pending):
                        publish('保留预热网页等待开抢，普通目标稍后继续')
                        timing.wait(page, reserved(waiting))
                        continue
                    if not pending and waiting:
                        publish('等待抢购准备或开抢时刻，等待期间不读取空位')
                        timing.wait(page, waiting)
                        continue
                    pending.sort(key=lambda g: not (g.rush.enabled and timing.state(g)['stage'] in {'首秒加速', '短时加速'}))
                    if any(g.rush.enabled and timing.prepared.get(g.id, {}).get('date_open') is False for g in pending):
                        needs_load = True
                        for goal in pending:
                            if goal.id in timing.prepared:
                                timing.prepared[goal.id]['date_open'] = True
                if not pending:
                    if recurring:
                        when = next_daily(config)
                        message = f"每日重复已开启 · 下次生成 {when.strftime('%m-%d %H:%M')} · 等待期间不读取空位"
                        if message != daily_wait_message:
                            publish(message)
                            daily_wait_message = message
                        page.wait_for_timeout(250)
                        continue
                    return finish("needs_attention",attention_message(states))
                groups = list(dict.fromkeys((target_context(config, t), t.date) for g in pending for t in g.candidates))
                snapshots, read_failed, rush_fast, batch_limited = {}, False, False, False
                for context, day in groups:
                    if stop():
                        raise InterruptedError("用户停止监测")
                    publish(f'正在读取 {context[1]} / {context[2]} / {day}')
                    if (current_context != context or needs_load or (context, day) in unopened_dates
                            or time.monotonic() - last_calendar >= config.calendar_refresh_seconds):
                        seed = read(lambda context=context: load(context))
                        needs_load = seed is None
                        if seed is None:
                            read_failed = True
                            batch_limited=isinstance(last_read_error,ReadDeferred) and not isinstance(last_read_error,GridNotReady)
                            if batch_limited:
                                break
                            publish(f'{context[1]} / {context[2]} / {day} 读取未完成，继续检查其他场馆')
                            continue
                    if seed is not None and seed.get("active_date") == day:
                        shot, seed = seed, None
                    else:
                        shot = read(lambda day=day: date_read(day))
                    if shot is None:
                        needs_load, read_failed = not bool(page.locator('#apointmentDetails').count()), True
                        batch_limited=isinstance(last_read_error,ReadDeferred) and not isinstance(last_read_error,GridNotReady)
                        if batch_limited:
                            break
                        publish(f'{context[1]} / {context[2]} / {day} 读取未完成，继续检查其他场馆')
                        continue
                    snapshots[(context, day)] = shot
                    if shot.get('date_not_open'):
                        unopened_dates.add((context, day))
                        publish(f'{day} 尚未开放，下轮将重新加载日历')
                    else:
                        unopened_dates.discard((context, day))
                    checked = datetime.now(ZoneInfo(config.timezone)).isoformat()
                    for goal in pending:
                        if any((target_context(config, t), t.date) == (context, day) for t in goal.candidates):
                            states[goal.id].update(last_checked_at=checked, watch_state="monitoring",
                                                   check_count=states[goal.id]["check_count"] + 1)
                    publish(f"已检查 {context[1]} / {context[2]} / {day}，相关目标共享结果",
                            None if shot.get("date_not_open") else shot)
                    rushed = [g for g in pending if g.rush.enabled]
                    if submit and rushed and best(rushed[0], snapshots) is not None:
                        rush_fast = True
                        break
                if snapshots and not batch_limited:
                    if not read_failed:
                        pacer.healthy()
                    ready = [(g, best(g, snapshots)) for g in pending if not rush_fast or g.rush.enabled]
                    ready = [(g, t) for g, t in ready if t is not None]
                    publish(f"本轮已检查 {len(snapshots)} 个日期/场馆组合，发现 {len(ready)} 个可预约目标")
                    for goal, queued in ready:
                        attempted = set()
                        target = queued
                        while target is not None:
                            if stop():
                                raise InterruptedError("用户停止监测")
                            context, day = target_context(config, target), target.date
                            if context in attempted:
                                break
                            attempted.add(context)
                            try:
                                if current_context != context or needs_load:
                                    current = read(lambda context=context: load(context))
                                    needs_load = current is None
                                else:
                                    current = site.loaded_snapshot(expected_sport=effective.sport)
                                if current is not None and (current["active_date"] != day or selected_dirty):
                                    current = read(lambda day=day: date_read(day))
                                if current is None:
                                    needs_load, read_failed = True, True
                                    break
                                snapshots[(context, day)] = current
                                target = best(goal, snapshots, restricted=context)
                                if target is None or not site.select(target):
                                    target = best(goal, snapshots)
                                    continue
                                selected_dirty = True
                                detail = {"date": day, "court": target.court, "slots": target.slots,
                                          "venue": effective.venue, "sport": effective.sport}
                                if not submit:
                                    states[goal.id].update(status="preview", detail=detail)
                                    break
                                button = site.prepare_submit(target)
                            except GridNotReady as exc:
                                defer(exc)
                                states[goal.id]["watch_state"] = "backoff"
                                needs_load, read_failed = not bool(page.locator('#apointmentDetails').count()), True
                                break
                            if stop():
                                raise InterruptedError("用户停止监测")
                            pacer.check_denial()
                            # The final submit is outside all read-retry handlers.
                            key = config.goal_key(goal)
                            outcome = submit_once(page, button, config.for_target(target), target, journal, key)
                            detail.update(outcome)
                            states[goal.id].update(status=outcome["status"], detail=detail,
                                                   watch_state="completed" if outcome["status"] == "submitted" else "blocked")
                            journal.update(key, outcome["status"], detail | {"goal_id": goal.id, "goal_name": goal.name})
                            completed = dict(states[goal.id]) if outcome["status"] == "submitted" else None
                            publish("目标已下单，其他未完成目标继续监测" if completed else outcome["message"], completed=completed)
                            needs_load, seed = True, None
                            break
                if not recurring and all(s["status"] == "submitted" for s in states.values()):
                    return finish("completed", "全部预约目标已提交成功；未执行付款")
                if not submit:
                    return finish("pending" if read_failed else "preview", "预览已结束，未提交订单")
                if not recurring and not any(s["status"] in {"pending", "rejected"} for s in states.values()):
                    return finish("needs_attention",attention_message(states))
                if not recurring and not config.monitor_until_complete and time.monotonic() >= deadline:
                    return finish("pending", "本轮监测到时，未完成目标已保留")
                seed = None
                publish("等待空位，继续检查全部未完成目标")
            except RushBoundary:
                preparing_now = False
                seed = None
                continue
        return finish("stopped", "已停止监测，进度已保留")
    except AccessDenied as exc:
        return finish("access_limited", str(exc))
    except InterruptedError:
        return finish("stopped", "已停止监测，进度已保留")
    finally:
        if site is not None:
            site.close()
        page.remove_listener("response", pacer.observe)
