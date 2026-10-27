"""One shared read budget for all date watchers, with explicit server backoff."""

from datetime import timezone
from collections import deque
from email.utils import parsedate_to_datetime
import random
import math
import time
from urllib.parse import urlsplit

READ_ENDPOINTS = ("/manage/fieldDetail/queryFieldSituation",
                  "/manage/fieldDetail/queryFieldReserveSituationIsFull")


# ==========================================
# Function: Pause monitoring when the website explicitly refuses access.
# Method: Keep access denial distinct from a temporarily unavailable slot.
# ==========================================
class AccessDenied(RuntimeError):
    pass


# ==========================================
# Function: Signal a server-requested cooldown without retrying immediately.
# Method: Propagate a recoverable read error to the shared scheduler.
# ==========================================
class ReadDeferred(RuntimeError):
    pass


# ==========================================
# Function: Request a bounded browser-session recovery after consecutive read-only grid failures.
# Method: Keep stuck-page recovery distinct from server throttles, access denial and order submission outcomes.
# ==========================================
class GridReadStalled(RuntimeError):
    pass


# ==========================================
# Function: Interpret Retry-After without ignoring a long server cooldown.
# Method: Accept delta seconds and HTTP dates; reject invalid or past values.
# ==========================================
def retry_after_seconds(value, wall_now):
    if not value:
        return 0.0
    try:
        seconds = float(value)
        if seconds >= 0 and seconds < float("inf"):
            return seconds
    except (ValueError, TypeError):
        pass
    try:
        stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, stamp.timestamp() - wall_now)
    except (ValueError, TypeError, OverflowError):
        return 0.0


# ==========================================
# Function: Pace every monitoring read across all goals and dates.
# Method: Share one monotonic deadline, additive jitter, and exponential cooldown.
# ==========================================
class ReadPacer:
    # ==========================================
    # Function: Initialize an account-wide monitoring budget.
    # Method: Permit the first read immediately, then enforce configured spacing.
    # ==========================================
    def __init__(self, config, clock=None, wall=None, jitter=None):
        self.config = config
        self.clock = clock or time.monotonic
        self.wall = wall or time.time
        self.jitter = jitter or random.uniform
        self.next_allowed = self.clock()
        self.cooldown_until = self.clock()
        self.failures = 0
        self.reads = 0
        self.blocked = False
        self.reason = ""
        self.server_error = None
        self.rush_windows = []
        self._rush_phase = None
        self._rush_counts = {}
        self._recent_reads = deque()
        self.last_read = None
        self.grid_failures = 0
        self.adaptive_level = self.healthy_cycles = 0
        self._server_feedback_read = None

    # ==========================================
    # Function: Share upcoming timed-release windows across every goal and venue.
    # Method: Retain account-wide counters instead of granting an independent burst to each goal.
    # ==========================================
    def set_rush_windows(self, windows):
        self.rush_windows = list(windows)

    # ==========================================
    # Function: Select a bounded serial cadence for the active release phase.
    # Method: Prefer the earliest active release, cap first-second leases and preserve normal pacing afterward.
    # ==========================================
    def cadence(self):
        now = self.clock()
        active = [(key, start, rush) for key, start, rush in self.rush_windows
                  if start <= now < start + 1 + rush.followup_seconds]
        if not active or self.adaptive_level:
            interval=min(self.config.poll_interval_seconds * 2**self.adaptive_level,
                         max(self.config.poll_interval_seconds,600))
            phase, wait_until = None, now
        else:
            key, start, rush = min(active, key=lambda item: item[1])
            initial = now < start + 1
            phase = (key, start, '首秒加速' if initial else '短时加速')
            interval = (rush.first_second_interval_ms if initial else rush.followup_interval_ms) / 1000
            wait_until = now
            if initial:
                while self._recent_reads and self._recent_reads[0] <= now - 1:
                    self._recent_reads.popleft()
                if self._rush_counts.get((key, start), 0) >= 4:
                    wait_until = start + 1
                elif len(self._recent_reads) >= 4:
                    wait_until = self._recent_reads[0] + 1
        if phase != self._rush_phase:
            if phase is not None and phase[-1] == '首秒加速':
                self.next_allowed = min(self.next_allowed, max(phase[1], (self.last_read + interval) if self.last_read is not None else now))
            elif self.last_read is not None:
                self.next_allowed = max(self.next_allowed, self.last_read + interval)
            self._rush_phase = phase
        return interval, wait_until

    # ==========================================
    # Function: Expose the next permitted access time to the graphical console.
    # Method: Translate the monotonic deadline to a wall-clock timestamp.
    # ==========================================
    def state(self):
        interval, quota = self.cadence()
        remaining = max(0.0, max(self.next_allowed, self.cooldown_until, quota) - self.clock())
        return {"interval_seconds": interval,
                "jitter_ratio": self.config.poll_jitter_ratio,
                "next_read_at": self.wall() + remaining, "wait_seconds": round(remaining, 1),
                "cooling_down": self.cooldown_until > self.clock(),
                "reason": self.reason, "reads": self.reads, "access_denied": self.blocked,
                "adaptive_level": self.adaptive_level, "grid_failures": self.grid_failures,
                "rush_phase": self._rush_phase[-1] if self._rush_phase else None}

    # ==========================================
    # Function: Wait cooperatively before a single network-producing read.
    # Method: Pump browser events so responses, manual verification, and stop remain live.
    # ==========================================
    def wait(self, page, stop, progress=None, wake=None):
        self.check_denial()
        if progress:
            progress(self.state())
        while not stop():
            if wake and wake():
                return False
            self.check_denial()
            interval, quota = self.cadence()
            remaining = max(self.next_allowed, self.cooldown_until, quota) - self.clock()
            if remaining <= 0:
                now = self.clock()
                jitter = 0 if self._rush_phase else self.jitter(0, self.config.poll_jitter_ratio)
                self.next_allowed = math.nextafter(now + interval * (1 + jitter), float('inf'))
                self.last_read = now
                self._recent_reads.append(now)
                while self._recent_reads and self._recent_reads[0] < now - 1:
                    self._recent_reads.popleft()
                if self._rush_phase and self._rush_phase[-1] == '首秒加速':
                    key = self._rush_phase[:2]
                    self._rush_counts[key] = self._rush_counts.get(key, 0) + 1
                self.reads += 1
                return True
            future = [start - self.clock() for _, start, _ in self.rush_windows if start > self.clock()]
            page.wait_for_timeout(max(.000001, min(200, remaining * 1000, min(future, default=1) * 1000)))
        return False

    # ==========================================
    # Function: Detect explicit rate limits on the site's native availability requests.
    # Method: Observe same-origin response status and Retry-After headers only.
    # ==========================================
    def observe(self, response):
        parsed, origin = urlsplit(response.url), urlsplit(self.config.booking_url)
        if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
            return
        booking_document = (parsed.path.rstrip("/") == origin.path.rstrip("/") and
                            getattr(getattr(response, "request", None), "resource_type", None) == "document")
        if not booking_document and not any(parsed.path.endswith(path) for path in READ_ENDPOINTS):
            return
        if response.status == 403:
            self.blocked = True
            self.reason = "网站返回 HTTP 403，已暂停监测，请稍后人工检查"
        elif response.status in {429, 500, 502, 503, 504}:
            retry_after=response.headers.get('retry-after')
            if self._server_feedback_read == self.reads:
                self.cooldown_until=max(self.cooldown_until,self.clock()+retry_after_seconds(retry_after,self.wall()))
                self.reason=f'网站返回 HTTP {response.status}；全局退避 {max(0,self.cooldown_until-self.clock()):.0f} 秒'
            else:
                self._server_feedback_read=self.reads
                self.defer(f"网站返回 HTTP {response.status}",retry_after,server=True)
            self.server_error = self.reason

    # ==========================================
    # Function: Increase delay after network errors or a server throttle response.
    # Method: Exponentially back off, always respecting any longer Retry-After.
    # ==========================================
    def defer(self, reason, retry_after=None, server=False):
        self.failures += 1
        self.healthy_cycles=0
        self.adaptive_level=min(3,self.adaptive_level+1)
        if server:
            self.grid_failures=0
        automatic = min(self.config.backoff_max_seconds,
                        max(30.0, self.config.poll_interval_seconds) * 2 ** min(self.failures - 1, 12))
        delay = max(automatic, retry_after_seconds(retry_after, self.wall()))
        self.cooldown_until = max(self.cooldown_until, self.clock() + delay)
        self.reason = f"{reason}；全局退避 {delay:.0f} 秒"

    # ==========================================
    # Function: Detect a stuck read cycle without treating HTTP throttling as a reason to rotate browsers.
    # Method: Count only unconfirmed grid/action timeouts and request recovery before installing another long delay.
    # ==========================================
    def grid_failed(self, reason):
        self.grid_failures+=1
        if self.grid_failures>=2:
            raise GridReadStalled(f'连续 {self.grid_failures} 次预约网格读取超时：{reason}')

    # ==========================================
    # Function: Stop before another read if an access denial was observed.
    # Method: Raise a dedicated error without probing the website again.
    # ==========================================
    def check_denial(self):
        if self.blocked:
            raise AccessDenied(self.reason)

    # ==========================================
    # Function: Reject a snapshot obtained during a throttled response.
    # Method: Consume the server error once while retaining the shared cooldown.
    # ==========================================
    def check_response(self):
        self.check_denial()
        if self.server_error:
            reason, self.server_error = self.server_error, None
            raise ReadDeferred(reason)

    # ==========================================
    # Function: Restore normal cadence after a confirmed healthy read.
    # Method: Require a complete healthy cycle before clearing failures and three cycles before reducing adaptive slowdown.
    # ==========================================
    def healthy(self, complete_cycle=True):
        if not complete_cycle or self.server_error:
            return
        self.failures = 0
        self.grid_failures=0
        self.healthy_cycles+=1
        if self.healthy_cycles>=3:
            self.adaptive_level=max(0,self.adaptive_level-1)
            self.healthy_cycles=0
        if self.clock() >= self.cooldown_until:
            self.reason = (f'错误恢复观察中，全局读取间隔暂为 {self.cadence()[0]:g} 秒' if self.adaptive_level else '')
