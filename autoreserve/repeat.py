"""Offline copies and deterministic daily occurrences; no network or GUI dependency."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from datetime import datetime as CalendarDateTime
import uuid
from zoneinfo import ZoneInfo

from .config import Goal


# ==========================================
# Function: Copy all ranked alternatives to explicitly selected new dates.
# Method: Deep-copy the source, replace dates, and allocate independent order identities.
# ==========================================
def copy_goals(source, dates):
    days = list(dict.fromkeys(dates))
    if not days or len(days) > 31:
        raise ValueError("请选择 1–31 个日期")
    result = []
    for day in days:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("复制日期必须为 YYYY-MM-DD")
        goal = deepcopy(source)
        rush = goal.get('rush') or {}
        if rush.get('at'):
            original = date.fromisoformat(source['candidates'][0]['date'])
            stamp = rush['at']
            if len(stamp) == 8 and stamp[2] == ':':
                stamp = (original - timedelta(days=source.get('days_ahead', 2))).isoformat() + 'T' + stamp
            value = datetime.fromisoformat(stamp)
            rush['at'] = (value + timedelta(days=(date.fromisoformat(day) - original).days)).isoformat()
        goal.update(id="goal-" + uuid.uuid4().hex[:16], repeat_id=None)
        for candidate in goal["candidates"]:
            candidate["date"] = day
        result.append(goal)
    return result


# ==========================================
# Function: Resolve a recurring release and its preparation boundary on the execution weekday.
# Method: Use the rush clock time when enabled and preserve the existing minute schedule otherwise.
# ==========================================
def occurrence_time(rule, day, zone):
    clock = rule.rush.at if rule.rush.enabled else rule.at + ':00'
    hour, minute, second = map(int, clock.split(':'))
    return CalendarDateTime(day.year, day.month, day.day, hour, minute, second, tzinfo=zone)


# ==========================================
# Function: Materialize a timed recurring goal early enough to complete its preparation.
# Method: Subtract lead time only for enabled rush templates, including windows that cross midnight.
# ==========================================
def preparation_time(rule, day, zone):
    release = occurrence_time(rule, day, zone)
    return release - timedelta(seconds=rule.rush.prewarm_seconds if rule.rush.enabled else 0)


# ==========================================
# Function: Find the next enabled weekday occurrence not yet materialized.
    # Method: Search beyond explicitly skipped weekdays while preserving deterministic rule/day identities.
# ==========================================
def next_daily(config, now=None):
    now = now or datetime.now(ZoneInfo(config.timezone))
    now = now.astimezone(ZoneInfo(config.timezone))
    identities = {g.id for g in config.goals}
    times = []
    for rule in config.repeat_rules:
        if not rule.enabled:
            continue
        for offset in range(7*(len(rule.skip_dates)+1)+1):
            day = now.date() + timedelta(days=offset)
            if day.weekday() not in rule.weekdays or day.isoformat() in rule.skip_dates or f"daily-{rule.id}-{day.isoformat()}" in identities:
                continue
            when = preparation_time(rule, day, ZoneInfo(config.timezone))
            times.append(max(when, now))
            break
    return min(times) if times else None


# ==========================================
# Function: Materialize today's enabled weekday rules without duplicate orders across restarts.
# Method: Keep historical occurrences until explicitly deleted and assign one stable occurrence per rule/day.
# ==========================================
def materialize_daily(config, now=None, records=None):
    now = now or datetime.now(ZoneInfo(config.timezone))
    now = now.astimezone(ZoneInfo(config.timezone))
    today, changed = now.date(), False
    kept = list(config.goals)
    identities = {g.id for g in kept}
    for rule in config.repeat_rules:
        for execution_day in (today, today + timedelta(days=1)):
            if (not rule.enabled or execution_day.weekday() not in rule.weekdays or execution_day.isoformat() in rule.skip_dates
                    or now < preparation_time(rule, execution_day, ZoneInfo(config.timezone))):
                continue
            identity = f"daily-{rule.id}-{execution_day.isoformat()}"
            if identity in identities:
                continue
            day = (execution_day + timedelta(days=rule.days_ahead)).isoformat()
            candidates = [replace(t, date=day) for t in rule.candidates]
            # Freeze inherited routes so later default changes cannot redirect a repeated goal.
            from .config import BookingContext
            context = BookingContext(config.booking_url, config.venue, config.sport) if any(t.context is None for t in candidates) else None
            candidates = [replace(t, context=t.context or context) for t in candidates]
            rush = replace(rule.rush, at=occurrence_time(rule, execution_day, ZoneInfo(config.timezone)).isoformat()) if rule.rush.enabled else replace(rule.rush, at=None)
            kept.append(Goal(identity, rule.name, candidates, repeat_id=rule.id, rush=rush))
            identities.add(identity)
            changed = True
    if len(kept) > 100 or sum(len(g.candidates) for g in kept) > 1000:
        raise ValueError("重复目标达到工作区上限，请在模板下方删除历史执行目标或减少候选；订单记录会保留")
    if changed:
        config.goals = kept
    return changed
