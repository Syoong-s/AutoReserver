"""Per-goal release timing, same-origin clock estimation and bounded serial rush windows."""

from datetime import datetime
from email.utils import parsedate_to_datetime
import math
import time
from urllib.parse import urlsplit, urlunsplit

from .config import parse_start


# ==========================================
# Function: Restart a normal read batch when a timed goal becomes eligible.
# Method: Interrupt only the pending budget wait, without treating scheduling as a network failure.
# ==========================================
class RushBoundary(Exception):
    pass


# ==========================================
# Function: Estimate server time without changing the host clock or using unrelated shopping-site APIs.
# Method: Anchor wall time to a monotonic clock and retain explicit HTTP Date precision bounds.
# ==========================================
class ServerClock:
    # ==========================================
    # Function: Establish a stable local fallback before any network preparation.
    # Method: Capture wall and monotonic anchors together and inject clocks for deterministic verification.
    # ==========================================
    def __init__(self, monotonic=None, wall=None):
        self.monotonic = monotonic or time.monotonic
        self.wall = wall or time.time
        self.anchor_mono, self.anchor_epoch = self.monotonic(), self.wall()
        self.offset = 0.0
        self.uncertainty = None
        self.synced_at = None
        self.message = '使用本地时间，尚未校时'

    # ==========================================
    # Function: Read the estimated server clock without being affected by later OS clock jumps.
    # Method: Advance the selected epoch only through monotonic elapsed time.
    # ==========================================
    def now(self):
        return self.anchor_epoch + self.monotonic() - self.anchor_mono

    # ==========================================
    # Function: Convert a configured release epoch into a stable monotonic deadline.
    # Method: Apply the optional manual server-ahead correction without adjusting the host clock.
    # ==========================================
    def deadline(self, epoch, manual_ms=0):
        return self.anchor_mono + epoch - self.anchor_epoch - manual_ms / 1000

    # ==========================================
    # Function: Reject cached, malformed or implausible clock samples.
    # Method: Estimate at the RTT midpoint and retain the HTTP Date half-second quantization uncertainty.
    # ==========================================
    def sample(self, headers, start_mono, end_mono, start_wall, end_wall):
        try:
            if float(headers.get('age', '0')) > 0:
                return None
            stamp = parsedate_to_datetime(headers['date'])
            rtt = end_mono - start_mono
            if stamp.tzinfo is None or not 0 <= rtt <= 3 or abs((end_wall - start_wall) - rtt) > .25:
                return None
            server_midpoint = stamp.timestamp() + .5
            offset = server_midpoint - (start_wall + end_wall) / 2
            if not math.isfinite(offset) or abs(offset) > 300:
                return None
            return {'offset': offset, 'uncertainty': .5 + rtt / 2,
                    'anchor_mono': end_mono, 'anchor_epoch': server_midpoint + rtt / 2}
        except (KeyError, ValueError, TypeError, OverflowError):
            return None

    # ==========================================
    # Function: Calibrate from the booking origin with a small, explicitly bounded set of HEAD requests.
    # Method: Pick the lowest-RTT uncached sample, honor throttling and dispose every response.
    # ==========================================
    def calibrate(self, page, url, stop, pacer):
        parsed = urlsplit(url)
        samples = []
        for index in range(3):
            if stop():
                raise InterruptedError('已停止抢购准备')
            pacer.check_denial()
            if pacer.cooldown_until > pacer.clock():
                break
            response = None
            before_mono, before_wall = self.monotonic(), self.wall()
            endpoint = urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                                   '__autoreserve_clock=' + str(time.monotonic_ns()), ''))
            try:
                response = page.request.head(endpoint, headers={'Cache-Control': 'no-cache', 'Pragma': 'no-cache'},
                                             timeout=1500, max_redirects=0)
                after_mono, after_wall = self.monotonic(), self.wall()
                if response.status == 403:
                    pacer.blocked, pacer.reason = True, '校时请求返回 HTTP 403，已暂停监测'
                    pacer.check_denial()
                if response.status in {429, 503}:
                    pacer.defer('校时请求返回 HTTP ' + str(response.status), response.headers.get('retry-after'))
                    break
                if 200 <= response.status < 300:
                    value = self.sample(response.headers, before_mono, after_mono, before_wall, after_wall)
                    if value:
                        samples.append(value)
            except Exception as error:
                from .monitoring import AccessDenied
                if isinstance(error, (AccessDenied, InterruptedError)):
                    raise
            finally:
                if response is not None:
                    response.dispose()
            if index < 2:
                page.wait_for_timeout(250)
        if samples:
            best = min(samples, key=lambda item: item['uncertainty'])
            self.anchor_mono, self.anchor_epoch = best['anchor_mono'], best['anchor_epoch']
            self.offset, self.uncertainty = best['offset'], best['uncertainty']
            self.synced_at = self.monotonic()
            self.message = f'HTTP Date 估算偏差 {self.offset * 1000:+.0f}ms · 误差约 ±{self.uncertainty * 1000:.0f}ms'
        else:
            self.anchor_mono, self.anchor_epoch = self.monotonic(), self.wall()
            self.offset, self.uncertainty, self.synced_at = 0.0, None, None
            self.message = '未取得可靠的同源时间，使用本地时间；可在设置中补偿偏差'
        return self.message


# ==========================================
# Function: Gate preparation, release and accelerated reads independently for each booking goal.
# Method: Share one clock and account pacer while retaining per-goal deadlines and preparation status.
# ==========================================
class RushCoordinator:
    # ==========================================
    # Function: Initialize a monitor session without network activity before a goal's preparation window.
    # Method: Store normal-start compatibility and defer all server-clock sampling until preparation.
    # ==========================================
    def __init__(self, config, pacer, clock=None):
        self.config, self.pacer = config, pacer
        self.clock = clock or ServerClock(pacer.clock, pacer.wall)
        self.local_clock = ServerClock(pacer.clock, pacer.wall)
        self.local_clock.message = '使用本地时间（此目标关闭自动校时）'
        self.prepared = {}
        self.calibration_attempt = None
        self.normal_deadline = (pacer.clock() + parse_start(config.start_at, config.timezone).timestamp() - pacer.wall()
                                if config.start_at else None)

    # ==========================================
    # Function: Resolve one goal's exact release deadline.
    # Method: Parse its timezone-aware/naive ISO timestamp and apply only that goal's manual correction.
    # ==========================================
    def deadline(self, goal):
        epoch = parse_start(goal.rush.at, self.config.timezone).timestamp()
        clock = self.clock if goal.rush.sync_clock else self.local_clock
        return clock.deadline(epoch, goal.rush.manual_offset_ms)

    # ==========================================
    # Function: Publish current rush windows to the shared account read budget.
    # Method: Use stable goal IDs and monotonic deadlines, including future windows that can wake a normal wait.
    # ==========================================
    def update(self, goals):
        self.pacer.set_rush_windows([(g.id, self.deadline(g), g.rush) for g in goals if g.rush.enabled])

    # ==========================================
    # Function: Decide whether selection and submission may begin for one goal.
    # Method: Require the rush release deadline or the legacy normal-start gate before considering stock.
    # ==========================================
    def released(self, goal):
        deadline = self.deadline(goal) if goal.rush.enabled else self.normal_deadline
        return deadline is None or self.pacer.clock() >= deadline

    # ==========================================
    # Function: Return pending rush goals whose preparation time has arrived.
    # Method: Keep distant targets completely offline and prepare each occurrence only once.
    # ==========================================
    def preparing(self, goals):
        now = self.pacer.clock()
        return [g for g in goals if g.rush.enabled and g.id not in self.prepared
                and self.deadline(g) - g.rush.prewarm_seconds <= now < self.deadline(g)]

    # ==========================================
    # Function: Reuse one recent same-origin calibration for nearby releases.
    # Method: Limit network clock sampling to preparation windows and at most once per five minutes.
    # ==========================================
    def calibrate(self, page, goals, stop):
        if any(g.rush.sync_clock for g in goals) and (self.calibration_attempt is None or self.pacer.clock() - self.calibration_attempt > 300):
            self.calibration_attempt = self.pacer.clock()
            return self.clock.calibrate(page, self.config.booking_url, stop, self.pacer)
        return self.clock.message

    # ==========================================
    # Function: Wait only until the next preparation or release boundary without blocking browser events.
    # Method: Use short native waits and never read inventory while every goal is still gated.
    # ==========================================
    def wait(self, page, goals):
        boundaries = []
        now = self.pacer.clock()
        for goal in goals:
            if goal.rush.enabled:
                release = self.deadline(goal)
                boundaries.append(release if goal.id in self.prepared else release - goal.rush.prewarm_seconds)
            elif self.normal_deadline:
                boundaries.append(self.normal_deadline)
        remaining = min((d - now for d in boundaries if d > now), default=.2)
        page.wait_for_timeout(max(1, min(200, remaining * 1000)))

    # ==========================================
    # Function: Expose useful per-goal countdown and calibration quality to the native workspace.
    # Method: Keep timing metadata separate from persistent order status and include approximate precision.
    # ==========================================
    def state(self, goal):
        if not goal.rush.enabled:
            return None
        elapsed = self.pacer.clock() - self.deadline(goal)
        stage = ('等待准备' if elapsed < -goal.rush.prewarm_seconds else '提前准备' if elapsed < 0
                 else '首秒加速' if elapsed < 1 else '短时加速' if elapsed < 1 + goal.rush.followup_seconds else '常规监测')
        clock = self.clock if goal.rush.sync_clock else self.local_clock
        return {'stage': stage, 'remaining_seconds': max(0, -elapsed), 'at': goal.rush.at,
                'deadline_monotonic': self.deadline(goal),
                'prepared': goal.id in self.prepared, 'clock': clock.message,
                'offset_ms': clock.offset * 1000 + goal.rush.manual_offset_ms,
                'uncertainty_ms': clock.uncertainty * 1000 if clock.uncertainty is not None else None}
