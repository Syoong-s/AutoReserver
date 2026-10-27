"""Deterministic request-budget and backoff tests; never wait in real time."""

from datetime import datetime, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from autoreserve.config import Config, load_config
from autoreserve.monitoring import AccessDenied, ReadDeferred, ReadPacer, retry_after_seconds


# ==========================================
# Function: Model elapsed browser waiting without wall-clock sleeps.
# Method: Advance the same virtual monotonic clock used by the production pacer.
# ==========================================
class FakeClock:
    # ==========================================
    # Function: Initialize a deterministic clock.
    # Method: Keep monotonic and wall times related by a fixed epoch offset.
    # ==========================================
    def __init__(self):
        self.now = 0.0

    # ==========================================
    # Function: Read simulated monotonic time.
    # Method: Return accumulated elapsed seconds.
    # ==========================================
    def monotonic(self):
        return self.now

    # ==========================================
    # Function: Read simulated wall time.
    # Method: Add a fixed Unix epoch to elapsed seconds.
    # ==========================================
    def wall(self):
        return 1700000000 + self.now

    # ==========================================
    # Function: Simulate Playwright's browser-event-pumping wait.
    # Method: Advance virtual time by the requested milliseconds.
    # ==========================================
    def wait_for_timeout(self, milliseconds):
        self.now += milliseconds / 1000


# ==========================================
# Function: Verify shared pacing and explicit server feedback behavior.
# Method: Exercise real pacer logic against the virtual clock.
# ==========================================
class MonitoringTests(unittest.TestCase):
    # ==========================================
    # Function: Create a deterministic global limiter.
    # Method: Fix jitter at its configured maximum so spacing is measurable.
    # ==========================================
    def setUp(self):
        self.clock = FakeClock()
        self.pacer = ReadPacer(Config(), clock=self.clock.monotonic, wall=self.clock.wall,
                               jitter=lambda low, high: high)

    # ==========================================
    # Function: Prevent more goals from multiplying the read frequency.
    # Method: Reserve three date reads and require shared 18-second spacing.
    # ==========================================
    def test_all_dates_share_one_budget(self):
        observed = []
        for _ in range(3):
            self.assertTrue(self.pacer.wait(self.clock, lambda: False))
            observed.append(self.clock.now)
        self.assertAlmostEqual(observed[1] - observed[0], 18.0)
        self.assertAlmostEqual(observed[2] - observed[1], 18.0)
        self.assertEqual(self.pacer.reads, 3)

    # ==========================================
    # Function: Ensure jitter cannot shorten the user's minimum interval.
    # Method: Use zero jitter and require at least the configured base spacing.
    # ==========================================
    def test_minimum_spacing_is_preserved(self):
        self.pacer.jitter = lambda low, high: 0
        self.pacer.wait(self.clock, lambda: False)
        self.pacer.wait(self.clock, lambda: False)
        self.assertAlmostEqual(self.clock.now, 15)

    # ==========================================
    # Function: Respect server cooldowns longer than the configured automatic cap.
    # Method: Observe HTTP 429 Retry-After=120 with an automatic cap of 30 seconds.
    # ==========================================
    def test_retry_after_is_not_capped(self):
        self.pacer.config.backoff_max_seconds = 30
        response = SimpleNamespace(url="https://sports.sjtu.edu.cn/manage/fieldDetail/queryFieldSituation",
                                   status=429, headers={"retry-after": "120"})
        self.pacer.observe(response)
        with self.assertRaises(ReadDeferred):
            self.pacer.check_response()
        self.pacer.wait(self.clock, lambda: False)
        self.assertAlmostEqual(self.clock.now, 120)

    # ==========================================
    # Function: Increase delay for repeated failures and recover normal cadence.
    # Method: Check 30/60/90-second delays under a 90-second cap.
    # ==========================================
    def test_exponential_backoff_and_recovery(self):
        self.pacer.config.backoff_max_seconds = 90
        for expected in (30, 60, 90, 90):
            self.pacer.defer("网络超时")
            self.assertAlmostEqual(self.pacer.cooldown_until-self.clock.now, expected)
            self.clock.now = self.pacer.cooldown_until
        self.pacer.healthy()
        self.pacer.defer("再次超时")
        self.assertAlmostEqual(self.pacer.cooldown_until-self.clock.now, 30)

    # ==========================================
    # Function: Avoid retrying a forbidden website response.
    # Method: Observe HTTP 403 and require a pause before any further reservation.
    # ==========================================
    def test_403_pauses_without_more_reads(self):
        self.pacer.observe(SimpleNamespace(url="https://sports.sjtu.edu.cn/manage/fieldDetail/queryFieldSituation",
                                          status=403, headers={}))
        with self.assertRaises(AccessDenied):
            self.pacer.wait(self.clock, lambda: False)
        self.assertEqual(self.pacer.reads, 0)

    # ==========================================
    # Function: Recognize a forbidden booking document as well as API refusals.
    # Method: Observe the same-origin booking document and stop before more reads.
    # ==========================================
    def test_document_403_also_pauses(self):
        response = SimpleNamespace(url="https://sports.sjtu.edu.cn/pc/", status=403,
                                   headers={}, request=SimpleNamespace(resource_type="document"))
        self.pacer.observe(response)
        self.assertTrue(self.pacer.blocked)

    # ==========================================
    # Function: Ignore static and third-party response errors.
    # Method: Limit feedback to same-origin availability endpoints.
    # ==========================================
    def test_feedback_scope(self):
        for url in ("https://example.com/manage/fieldDetail/queryFieldSituation", "https://sports.sjtu.edu.cn/favicon.ico"):
            self.pacer.observe(SimpleNamespace(url=url, status=403, headers={}))
        self.assertFalse(self.pacer.blocked)

    # ==========================================
    # Function: Keep long waits cancellable.
    # Method: Stop a 120-second cooldown immediately without another read.
    # ==========================================
    def test_stop_interrupts_backoff(self):
        self.pacer.defer("限流", "120")
        self.assertFalse(self.pacer.wait(self.clock, lambda: True))
        self.assertEqual(self.clock.now, 0)
        self.assertEqual(self.pacer.reads, 0)

    # ==========================================
    # Function: Parse both Retry-After formats and reject bad header values.
    # Method: Compare delta seconds and an equivalent HTTP timestamp.
    # ==========================================
    def test_retry_after_formats(self):
        now = self.clock.wall()
        value = format_datetime(datetime.fromtimestamp(now + 75, timezone.utc), usegmt=True)
        self.assertEqual(retry_after_seconds(value, now), 75)
        self.assertEqual(retry_after_seconds("120", now), 120)
        for invalid in (None, "bad", "NaN", "inf", "-1"):
            self.assertEqual(retry_after_seconds(invalid, now), 0)

    # ==========================================
    # Function: Upgrade the former three-second default safely.
    # Method: Normalize existing files while rejecting new sub-five-second settings.
    # ==========================================
    def test_legacy_interval_upgrade_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"poll_interval_seconds": 3}))
            self.assertEqual(load_config(path).poll_interval_seconds, 15)
        with self.assertRaises(ValueError):
            Config(poll_interval_seconds=3).validate()
        with self.assertRaises(ValueError):
            Config(poll_jitter_ratio=-0.2).validate()


if __name__ == "__main__":
    unittest.main()
