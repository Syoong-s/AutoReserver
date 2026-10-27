"""Strict configuration and local-time scheduling, with no third-party dependencies."""

from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit, unquote
from zoneinfo import ZoneInfo

HOME_URL = "https://sports.sjtu.edu.cn/pc/#/"
SITE_ORIGIN = "https://sports.sjtu.edu.cn"


# ==========================================
# Function: Represent one exact venue/project booking route and legacy goal scope.
# Method: Keep immutable context separate from candidate dates and priority.
# ==========================================
@dataclass(frozen=True)
class BookingContext:
    booking_url: str
    venue: str
    sport: str

    # ==========================================
    # Function: Reject incomplete or ambiguous context metadata.
    # Method: Require all route and display fields to be nonempty strings.
    # ==========================================
    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in (self.booking_url, self.venue, self.sport)):
            raise ValueError("场馆、项目与预约链接必须完整")


# ==========================================
# Function: Validate both inherited and explicit booking routes.
# Method: Restrict production routes to SJTU and permit loopback only in tests.
# ==========================================
def validate_context(context, allow_local=False, require_booking=True):
    u = urlsplit(context.booking_url)
    local = allow_local and u.scheme == "http" and u.hostname in {"127.0.0.1", "localhost"}
    if u.username or u.password or (not local and (u.scheme != "https" or u.netloc != "sports.sjtu.edu.cn" or u.path != "/pc/")):
        raise ValueError("预约链接必须是 https://sports.sjtu.edu.cn/pc/ 下的页面")
    if require_booking and not local and not re.fullmatch(r"/apointmentDetails/1/[^/]+/[^/]+/[^/]+", unquote(u.fragment)):
        raise ValueError("请选择目录中的具体场馆和项目")


# ==========================================
# Function: Validate an individual candidate reservation.
# Method: Require an ISO date, an exact court, and unique hourly slots.
# ==========================================
@dataclass(frozen=True)
class Target:
    date: str
    court: str
    slots: list[str]
    context: BookingContext | None = None

    # ==========================================
    # Function: Reject invalid reservation parameters.
    # Method: Compare canonical dates and slot strings with the site's hourly grid.
    # ==========================================
    def __post_init__(self):
        if self.context is not None and not isinstance(self.context, BookingContext):
            raise ValueError("候选场馆信息格式错误")
        if not isinstance(self.date, str) or date.fromisoformat(self.date).isoformat() != self.date:
            raise ValueError("date 必须是 YYYY-MM-DD")
        if not isinstance(self.court, str) or not self.court.strip():
            raise ValueError("court 不能为空")
        if not isinstance(self.slots, list) or not self.slots:
            raise ValueError("slots 必须是非空列表，例如 [\"14:00-15:00\"]")
        allowed = {f"{h:02d}:00-{h + 1:02d}:00" for h in range(7, 22)}
        if any(not isinstance(s, str) or s not in allowed for s in self.slots):
            raise ValueError("时段必须为 07:00–22:00 内的整点一小时，如 14:00-15:00")
        if len(set(self.slots)) != len(self.slots):
            raise ValueError("同一候选不能包含重复时段")
        hours = sorted(int(s[:2]) for s in self.slots)
        if hours != list(range(hours[0], hours[-1] + 1)):
            raise ValueError("一个候选内的多个时段必须连续")


# ==========================================
# Function: Describe a bounded, per-goal timed release without changing its order identity.
# Method: Keep the exact start time, preparation lead and serial polling cadence in one strict value.
# ==========================================
@dataclass
class RushSettings:
    enabled: bool = False
    at: str | None = None
    prewarm_seconds: float = 45.0
    first_second_interval_ms: int = 250
    followup_interval_ms: int = 1000
    followup_seconds: float = 8.0
    sync_clock: bool = True
    manual_offset_ms: float = 0.0

    # ==========================================
    # Function: Reject unbounded polling, ambiguous release times and invalid switch values.
    # Method: Validate numeric ranges and distinguish recurring HH:MM:SS from one-time ISO timestamps.
    # ==========================================
    def validate(self, repeating=False):
        if type(self.enabled) is not bool or type(self.sync_clock) is not bool:
            raise ValueError('抢购开关与校时开关必须为布尔值')
        for key, low, high in (('prewarm_seconds', 5, 600), ('first_second_interval_ms', 200, 1000),
                               ('followup_interval_ms', 1000, 15000), ('followup_seconds', 0, 30),
                               ('manual_offset_ms', -5000, 5000)):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'抢购 {key} 必须在 {low}–{high} 范围内')
        if self.enabled and not self.at:
            raise ValueError('请设置抢购时间')
        if self.at is not None:
            if not isinstance(self.at, str):
                raise ValueError('抢购时间必须为字符串')
            if repeating:
                if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]', self.at):
                    raise ValueError('重复目标的抢购时间必须为 HH:MM:SS')
            else:
                parse_start(self.at, 'Asia/Shanghai')
        return self


# ==========================================
# Function: Group prioritized alternatives under one required booking goal.
# Method: Give each goal a stable identity independent of candidate ordering.
# ==========================================
@dataclass
class Goal:
    id: str
    name: str
    candidates: list[Target]
    origin: BookingContext | None = None
    repeat_id: str | None = None
    rush: RushSettings = field(default_factory=RushSettings)

    # ==========================================
    # Function: Reject incomplete or ambiguous goals.
    # Method: Require a stable ID and alternatives belonging to the same date.
    # ==========================================
    def __post_init__(self):
        if isinstance(self.rush, dict):
            self.rush = RushSettings(**self.rush)
        if not isinstance(self.rush, RushSettings):
            raise ValueError('抢购设置格式错误')
        self.rush.validate()
        if self.repeat_id is not None and (not isinstance(self.repeat_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", self.repeat_id)):
            raise ValueError("重复规则 ID 无效")
        if self.origin is not None and not isinstance(self.origin, BookingContext):
            raise ValueError("目标来源信息格式错误")
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", self.id):
            raise ValueError("预约目标 ID 无效")
        if not isinstance(self.name, str) or len(self.name) > 120:
            raise ValueError("预约目标名称无效")
        if not isinstance(self.candidates, list) or not self.candidates:
            raise ValueError("每个预约目标至少需要一个场地/时段候选")
        if any(not isinstance(t, Target) for t in self.candidates):
            raise ValueError("候选格式错误")
        if len({t.date for t in self.candidates}) != 1:
            raise ValueError("同一目标的候选必须为同一日期；其他日期请添加新目标")
        identities = [(t.date, t.court, tuple(sorted(t.slots)), t.context) for t in self.candidates]
        if len(set(identities)) != len(identities):
            raise ValueError("同一目标内有重复候选")


# ==========================================
# Function: Store reusable daily booking alternatives independently of dated orders.
# Method: Validate the template as a goal and use local HH:MM plus a relative day offset.
# ==========================================
@dataclass
class RepeatRule:
    id: str
    name: str
    candidates: list[Target]
    days_ahead: int = 2
    at: str = "12:00"
    enabled: bool = True
    weekdays: list[int] = field(default_factory=lambda: list(range(7)))
    rush: RushSettings = field(default_factory=RushSettings)

    # ==========================================
    # Function: Reject ambiguous schedules before saving or starting monitoring.
    # Method: Validate nonempty execution weekdays, upcoming-week offsets, and candidate requirements.
    # ==========================================
    def __post_init__(self):
        if isinstance(self.rush, dict):
            self.rush = RushSettings(**self.rush)
        if not isinstance(self.rush, RushSettings):
            raise ValueError('抢购设置格式错误')
        self.rush.validate(repeating=True)
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", self.id):
            raise ValueError("重复规则 ID 无效")
        Goal(self.id, self.name, self.candidates)
        if type(self.days_ahead) is not int or not 0 <= self.days_ahead <= 7:
            raise ValueError("每天重复的日期偏移必须在 0–7 天内")
        if not isinstance(self.at, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", self.at):
            raise ValueError("每天启动时间必须为 HH:MM，例如 12:00")
        if type(self.enabled) is not bool:
            raise ValueError("重复规则 enabled 必须为布尔值")
        if (not isinstance(self.weekdays, list) or not self.weekdays
                or any(type(day) is not int or day not in range(7) for day in self.weekdays)
                or len(set(self.weekdays)) != len(self.weekdays)):
            raise ValueError("每周执行日必须选择至少一天，使用不重复的 0–6（周一到周日）")


# ==========================================
# Function: Represent a complete, configurable booking task.
# Method: Keep one captured venue URL and prioritized alternatives.
# ==========================================
@dataclass
class Config:
    booking_url: str = HOME_URL
    venue: str = ""
    sport: str = "健身房"
    targets: list[Target] = field(default_factory=list)
    goals: list[Goal] = field(default_factory=list)
    repeat_rules: list[RepeatRule] = field(default_factory=list)
    monitor_until_complete: bool = False
    start_at: str | None = None
    timezone: str = "Asia/Shanghai"
    poll_interval_seconds: float = 15.0
    poll_jitter_ratio: float = 0.2
    backoff_max_seconds: float = 600.0
    calendar_refresh_seconds: float = 300.0
    max_poll_seconds: float = 0.0
    action_timeout_ms: int = 10000
    result_timeout_seconds: float = 30.0
    verification_timeout_seconds: float = 180.0
    max_total_price: str = "0.00"
    headless: bool = False
    browser_executable: str | None = None
    workspace_version: int = 1
    initialized: bool = False

    # ==========================================
    # Function: Validate configuration before browser launch.
    # Method: Reject invalid types/routes and enforce timing ranges with an explicit zero-duration unlimited sentinel.
    # ==========================================
    def validate(self, require_targets=False, allow_local=False):
        if type(self.initialized) is not bool:
            raise ValueError("initialized 必须为 true 或 false")
        if type(self.workspace_version) is not int or self.workspace_version not in {1, 2}:
            raise ValueError("工作区版本无效")
        for name in ("venue", "sport", "timezone", "booking_url"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} 必须是字符串")
        if not self.sport.strip():
            raise ValueError("sport 不能为空")
        ZoneInfo(self.timezone)
        u = urlsplit(self.booking_url)
        local = allow_local and u.scheme == "http" and u.hostname in {"127.0.0.1", "localhost"}
        if not local and (u.scheme != "https" or u.netloc != "sports.sjtu.edu.cn" or u.path != "/pc/"):
            raise ValueError("booking_url 必须是 https://sports.sjtu.edu.cn/pc/ 下的页面")
        if u.username or u.password:
            raise ValueError("网址中不允许包含账号密码")
        if type(self.headless) is not bool:
            raise ValueError("headless 必须为 true 或 false")
        if type(self.monitor_until_complete) is not bool:
            raise ValueError("monitor_until_complete 必须为 true 或 false")
        if len({g.id for g in self.goals}) != len(self.goals):
            raise ValueError("预约目标 ID 不能重复")
        if len(self.goals) > 100 or sum(len(g.candidates) for g in self.goals) > 1000:
            raise ValueError("最多支持 100 个目标和 1000 个候选")
        if len(self.repeat_rules) > 10 or len({r.id for r in self.repeat_rules}) != len(self.repeat_rules):
            raise ValueError("最多支持 10 条每日规则，规则 ID 不能重复")
        if sum(len(r.candidates) for r in self.repeat_rules) > 1000:
            raise ValueError("每日规则最多包含 1000 个候选")
        all_targets = [*self.targets, *(t for g in self.goals for t in g.candidates),
                       *(t for r in self.repeat_rules for t in r.candidates)]
        for target in all_targets:
            if target.context:
                validate_context(target.context, allow_local)
        for goal in self.goals:
            if goal.origin:
                validate_context(goal.origin, allow_local)
        for name, low, high in (
            ("poll_interval_seconds", 5, 3600), ("poll_jitter_ratio", 0, 1),
            ("backoff_max_seconds", 30, 3600), ("calendar_refresh_seconds", 60, 3600),
            ("max_poll_seconds", 0, 86400),
            ("action_timeout_ms", 100, 120000), ("result_timeout_seconds", 1, 600),
            ("verification_timeout_seconds", 1, 1800),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} 必须在 {low}–{high} 范围内")
        if not isinstance(self.max_total_price, str):
            raise ValueError("max_total_price 请使用字符串，例如 \"0.00\"")
        try:
            money = Decimal(self.max_total_price)
            if not money.is_finite() or money < 0:
                raise ValueError("max_total_price 必须为非负金额")
        except InvalidOperation as exc:
            raise ValueError("max_total_price 不是有效金额") from exc
        if self.browser_executable is not None and not isinstance(self.browser_executable, str):
            raise ValueError("browser_executable 必须为当前系统的可执行文件路径或 null")
        if self.start_at is not None:
            parse_start(self.start_at, self.timezone)
        if require_targets:
            if not self.targets and not self.goals and not any(r.enabled for r in self.repeat_rules):
                raise ValueError("请先添加预约目标")
            needs_default = any(t.context is None for t in all_targets)
            if needs_default and not self.venue.strip():
                raise ValueError("请先运行 login 捕获实际场馆")
            if needs_default and not local and not re.fullmatch(r"/apointmentDetails/1/[^/]+/[^/]+/[^/]+", unquote(u.fragment)):
                raise ValueError("请先运行 login，在浏览器打开具体场馆的个人预约页再保存")
            today = datetime.now(ZoneInfo(self.timezone)).date()
            # Completed dated goals may remain visible after their date has passed.
            if not self.goals and any(date.fromisoformat(t.date) < today for t in self.targets):
                raise ValueError("targets 中不能包含已过去的日期")
        return self

    # ==========================================
    # Function: Represent an unlimited monitoring duration without serializing nonfinite config values.
    # Method: Convert the zero-duration sentinel to an infinite runtime deadline at each monitoring start.
    # ==========================================
    def monitor_deadline(self, now):
        return now + self.max_poll_seconds if self.max_poll_seconds > 0 else math.inf

    # ==========================================
    # Function: Calculate a stable identity for this task.
    # Method: Hash venue, sport, and all alternatives, excluding run timing.
    # ==========================================
    def task_key(self):
        values = []
        for target in self.targets:
            value = asdict(target)
            if target.context is None:
                value.pop("context")
            values.append(value)
        data = [self.booking_url, self.venue, self.sport, values]
        return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    # ==========================================
    # Function: Identify each slot independently of candidate ordering.
    # Method: Hash site, venue, sport, date, court, and individual hour.
    # ==========================================
    def slot_keys(self, target):
        effective = self.for_target(target)
        legacy = [hashlib.sha256(json.dumps([
            effective.booking_url, effective.venue, effective.sport, target.date, target.court, slot
        ], ensure_ascii=False).encode()).hexdigest() for slot in target.slots]
        # Equivalent personal routes can differ only in their selected project segment.
        parsed = urlsplit(effective.booking_url)
        segments = unquote(parsed.fragment).strip("/").split("/")
        venue_id = segments[2] if len(segments) == 5 and segments[0] == "apointmentDetails" else effective.booking_url
        canonical = [hashlib.sha256(json.dumps([
            "slot-v2", parsed.netloc, venue_id, effective.sport, target.date, target.court, slot
        ], ensure_ascii=False).encode()).hexdigest() for slot in target.slots]
        return list(dict.fromkeys([*legacy, *canonical]))

    # ==========================================
    # Function: Resolve a candidate's own venue/project with legacy default fallback.
    # Method: Preserve shared timing settings while selecting only this concrete candidate.
    # ==========================================
    def for_target(self, target):
        context = target.context
        return replace(self, booking_url=context.booking_url if context else self.booking_url,
                       venue=context.venue if context else self.venue, sport=context.sport if context else self.sport,
                       goals=[], targets=[target])

    # ==========================================
    # Function: Reuse the verified single-order adapter for one goal.
    # Method: Derive a configuration with only that goal's alternatives.
    # ==========================================
    def for_goal(self, goal):
        return replace(self, goals=[], targets=goal.candidates)

    # ==========================================
    # Function: Persist progress independently of priority changes.
    # Method: Hash the booking context and stable goal ID.
    # ==========================================
    def goal_key(self, goal):
        scope = [goal.origin.booking_url, goal.origin.venue, goal.origin.sport] if goal.origin else [self.booking_url, self.venue, self.sport]
        value = [*scope, "goal-v1", goal.id]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


# ==========================================
# Function: Parse scheduled start times consistently.
# Method: Accept an ISO datetime, applying the configured zone only if absent.
# ==========================================
def parse_start(value, timezone):
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
        raise ValueError("start_at 必须为完整日期时间，例如 2026-10-27T12:00:00+08:00")
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo(timezone))


# ==========================================
# Function: Read a strict JSON configuration.
# Method: Dataclasses reject misspelled keys; validation rejects invalid values.
# ==========================================
def load_config(path, allow_local=False):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    # Upgrade the old short default only when reading an existing file.
    value = data.get("poll_interval_seconds") if isinstance(data, dict) else None
    if type(value) in (int, float) and 0 < value < 5:
        data["poll_interval_seconds"] = 15.0
    return config_from_dict(data, allow_local=allow_local)


# ==========================================
# Function: Validate JSON received from the graphical interface.
# Method: Construct strict nested dataclasses before applying normal validation.
# ==========================================
def config_from_dict(data, allow_local=False):
    if not isinstance(data, dict) or not isinstance(data.get("targets", []), list):
        raise ValueError("配置必须是对象，targets 必须是列表")
    data = dict(data)
    if not isinstance(data.get("goals", []), list):
        raise ValueError("goals 必须是列表")
    data["targets"] = [target_from_dict(t) for t in data.get("targets", [])]
    data["goals"] = [Goal(**(g | {"candidates": [target_from_dict(t) for t in g.get("candidates", [])],
                                 "origin": BookingContext(**g["origin"]) if g.get("origin") else None,
                                 "rush": RushSettings(**g.get("rush", {}))}))
                     for g in data.get("goals", [])]
    if not isinstance(data.get("repeat_rules", []), list):
        raise ValueError("repeat_rules 必须是列表")
    data["repeat_rules"] = [RepeatRule(**(r | {"candidates": [target_from_dict(t) for t in r.get("candidates", [])],
                                             "rush": RushSettings(**r.get("rush", {}))}))
                            for r in data.get("repeat_rules", [])]
    return Config(**data).validate(allow_local=allow_local)


# ==========================================
# Function: Parse optional per-candidate venue context from persisted JSON.
# Method: Keep old three-field targets valid and reject malformed nested metadata.
# ==========================================
def target_from_dict(value):
    if not isinstance(value, dict):
        raise ValueError("候选必须是对象")
    data = dict(value)
    data["context"] = BookingContext(**data["context"]) if data.get("context") else None
    return Target(**data)


# ==========================================
# Function: Save a configuration atomically.
# Method: Replace a temporary UTF-8 JSON file in the same directory.
# ==========================================
def save_config(config, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


# ==========================================
# Function: Create a usable initial config without booking stale example dates.
# Method: Use tomorrow in the configured local zone.
# ==========================================
def example_config():
    tomorrow = datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=1)
    return Config(targets=[Target(tomorrow.isoformat(), "场地1", ["14:00-15:00"])])
