"""Deterministic release-clock, shared burst-budget, recurring and editor tests."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from autoreserve.config import Config, Goal, RepeatRule, RushSettings, Target, config_from_dict
from autoreserve.catalog import booking_url
from autoreserve.monitoring import ReadPacer
from autoreserve.repeat import materialize_daily, next_daily
from autoreserve.rush import RushCoordinator, ServerClock
from autoreserve.workspace import Workspace
from test_monitoring import FakeClock


# ==========================================
# Function: Verify release accuracy, bounded cadence and goal identity without network or real sleeps.
# Method: Drive the production clock/pacer with a virtual browser and strict persistent config.
# ==========================================
class RushTests(unittest.TestCase):
    # ==========================================
    # Function: Initialize one account limiter and a future dated booking goal.
    # Method: Use a fixed epoch shared by wall and monotonic test clocks.
    # ==========================================
    def setUp(self):
        self.clock = FakeClock()
        self.config = Config(booking_url=booking_url('student', '健身房'), venue='A馆', poll_interval_seconds=15, poll_jitter_ratio=0)
        self.pacer = ReadPacer(self.config, self.clock.monotonic, self.clock.wall, lambda a, b: 0)
        self.release = datetime.fromtimestamp(self.clock.wall() + 60, ZoneInfo('Asia/Shanghai'))
        self.target = Target(self.release.date().isoformat(), '场地1', ['14:00-15:00'])
        self.rush = RushSettings(enabled=True, at=self.release.isoformat(), sync_clock=False)
        self.goal = Goal('rush-goal', '抢购目标', [self.target], rush=self.rush)

    # ==========================================
    # Function: Reject unsafe intervals and distinguish template clock strings from dated releases.
    # Method: Round-trip the public JSON schema while exercising strict switches and timing ranges.
    # ==========================================
    def test_schema_and_old_defaults(self):
        self.config.goals = [self.goal]
        loaded = config_from_dict(asdict(self.config))
        self.assertEqual(loaded.goals[0].rush, self.rush)
        old = asdict(self.config)
        old['goals'][0].pop('rush')
        self.assertFalse(config_from_dict(old).goals[0].rush.enabled)
        for changes in ({'enabled': 1}, {'at': None}, {'first_second_interval_ms': 10},
                        {'prewarm_seconds': 0}, {'followup_interval_ms': 100}, {'manual_offset_ms': float('nan')}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.rush, **changes).validate()
        with self.assertRaises(ValueError):
            replace(self.rush, at='12:00:00').validate()
        self.assertTrue(replace(self.rush, at='12:00:00').validate(repeating=True).enabled)

    # ==========================================
    # Function: Preserve persistent order identity when only timed-release settings change.
    # Method: Compare the exact journal key before and after enabling a rush profile.
    # ==========================================
    def test_order_identity_unchanged(self):
        normal = replace(self.goal, rush=RushSettings())
        self.assertEqual(self.config.goal_key(normal), self.config.goal_key(self.goal))

    # ==========================================
    # Function: Prevent pre-release selection while making preparation available at the configured lead.
    # Method: Advance to just before, at, and after preparation/release deadlines.
    # ==========================================
    def test_preparation_and_release_gates(self):
        scheduler = RushCoordinator(self.config, self.pacer)
        self.assertFalse(scheduler.preparing([self.goal]))
        self.clock.now = 15
        self.assertEqual(scheduler.preparing([self.goal]), [self.goal])
        self.assertFalse(scheduler.released(self.goal))
        self.clock.now = 59.999
        self.assertFalse(scheduler.released(self.goal))
        self.clock.now = 60
        self.assertTrue(scheduler.released(self.goal))
        self.assertEqual(scheduler.state(self.goal)['stage'], '首秒加速')

    # ==========================================
    # Function: Limit all first-second watchers to one account-wide serial quota.
    # Method: Configure overlapping goal windows, issue leases and inspect their actual virtual timestamps.
    # ==========================================
    def test_first_second_quota_is_shared(self):
        rush = replace(self.rush, first_second_interval_ms=200)
        self.pacer.set_rush_windows([('first', 0, rush), ('second', .1, rush)])
        issued = []
        for _ in range(6):
            self.assertTrue(self.pacer.wait(self.clock, lambda: False))
            issued.append(self.clock.now)
        self.assertEqual(len([stamp for stamp in issued if stamp < 1]), 4)
        for stamp in issued:
            self.assertLessEqual(sum(stamp - 1 < other <= stamp for other in issued), 4)
        self.assertTrue(all(b - a >= .199999 for a, b in zip(issued, issued[1:])))
        self.clock.now = 40
        self.assertEqual(self.pacer.state()['interval_seconds'], 15)

    # ==========================================
    # Function: Wake an existing normal 15-second budget at the rush release boundary.
    # Method: Issue a preparation lease, then wait through a future first-second window.
    # ==========================================
    def test_rush_can_shorten_normal_wait_but_not_server_backoff(self):
        self.pacer.wait(self.clock, lambda: False)
        self.pacer.set_rush_windows([('rush', 5, self.rush)])
        self.pacer.wait(self.clock, lambda: False)
        self.assertAlmostEqual(self.clock.now, 5)
        self.pacer.defer('HTTP 429', '40')
        self.pacer.wait(self.clock, lambda: False)
        self.assertGreaterEqual(self.clock.now, 45)
        self.assertIsNone(self.pacer.state()['rush_phase'])

    # ==========================================
    # Function: Allow scheduling preemption without consuming an inventory-read lease.
    # Method: Interrupt the wait using the actual pacer wake callback.
    # ==========================================
    def test_budget_wake_does_not_issue_a_read(self):
        self.pacer.wait(self.clock, lambda: False)
        self.assertFalse(self.pacer.wait(self.clock, lambda: False, wake=lambda: True))
        self.assertEqual(self.pacer.reads, 1)

    # ==========================================
    # Function: Keep server calibration honest about HTTP Date quantization and RTT.
    # Method: Check a known offset and reject cached responses and wall-clock discontinuities.
    # ==========================================
    def test_clock_sample_precision_and_rejections(self):
        timer = ServerClock(self.clock.monotonic, self.clock.wall)
        date = datetime.fromtimestamp(1700000002, timezone.utc)
        sample = timer.sample({'date': format_datetime(date, usegmt=True)}, 0, .1, 1700000000, 1700000000.1)
        self.assertAlmostEqual(sample['offset'], 2.45)
        self.assertAlmostEqual(sample['uncertainty'], .55)
        self.assertIsNone(timer.sample({'date': format_datetime(date), 'age': '5'}, 0, .1, 1700000000, 1700000000.1))
        self.assertIsNone(timer.sample({'date': format_datetime(date)}, 0, .1, 1700000000, 1700000002))

    # ==========================================
    # Function: Limit real calibration attempts and release all response resources.
    # Method: Return three same-origin successful HEAD samples through a synthetic browser request context.
    # ==========================================
    def test_calibration_probes_are_same_origin_bounded_and_disposed(self):
        timer=ServerClock(self.clock.monotonic,self.clock.wall)
        response=SimpleNamespace(status=200,headers={
            'date':format_datetime(datetime.fromtimestamp(self.clock.wall(),timezone.utc),usegmt=True)},dispose=Mock())
        head=Mock(return_value=response)
        page=SimpleNamespace(request=SimpleNamespace(head=head),wait_for_timeout=self.clock.wait_for_timeout)
        timer.calibrate(page,self.config.booking_url,lambda:False,self.pacer)
        self.assertEqual(head.call_count,3)
        self.assertEqual(response.dispose.call_count,3)
        for call in head.call_args_list:
            self.assertTrue(call.args[0].startswith('https://sports.sjtu.edu.cn/pc/?__autoreserve_clock='))
            self.assertEqual(call.kwargs['max_redirects'],0)
        self.assertIsNotNone(timer.uncertainty)
        self.assertIn('误差约',timer.message)

    # ==========================================
    # Function: Apply calibration rate limits to the same account budget used for rush reads.
    # Method: Return one Retry-After response and require no additional probes or optimistic calibration.
    # ==========================================
    def test_calibration_429_stops_probes_and_defers_account(self):
        timer=ServerClock(self.clock.monotonic,self.clock.wall)
        timer.anchor_epoch+=2
        timer.offset,timer.uncertainty,timer.synced_at=2,.55,0
        response=SimpleNamespace(status=429,headers={'retry-after':'90'},dispose=Mock())
        head=Mock(return_value=response)
        page=SimpleNamespace(request=SimpleNamespace(head=head),wait_for_timeout=self.clock.wait_for_timeout)
        timer.calibrate(page,self.config.booking_url,lambda:False,self.pacer)
        self.assertEqual(head.call_count,1)
        response.dispose.assert_called_once()
        self.assertGreaterEqual(self.pacer.cooldown_until,90)
        self.assertIsNone(timer.uncertainty)
        self.assertEqual(timer.offset,0)
        self.assertAlmostEqual(timer.now(),self.clock.wall())
        self.assertIn('使用本地时间',timer.message)

    # ==========================================
    # Function: Keep release deadlines stable if the operating system changes its wall clock afterward.
    # Method: Anchor an estimated server epoch to monotonic time, then alter the wall source.
    # ==========================================
    def test_monotonic_clock_survives_wall_jump(self):
        timer = ServerClock(self.clock.monotonic, self.clock.wall)
        deadline = timer.deadline(1700000060)
        self.clock.wall = lambda: 1800000000
        self.clock.now = 10
        self.assertEqual(timer.deadline(1700000060), deadline)
        self.assertAlmostEqual(timer.now(), 1700000010)

    # ==========================================
    # Function: Materialize weekly rush goals at preparation time with the correct booking-day offset.
    # Method: Generate one Monday release before noon and retain its deterministic identity across calls.
    # ==========================================
    def test_weekly_materialization_uses_prewarm_time(self):
        now = datetime(2026, 10, 26, 11, 59, 14, tzinfo=ZoneInfo('Asia/Shanghai'))
        rule = RepeatRule('weekly-rush', '周一', [self.target], weekdays=[0], rush=replace(self.rush, at='12:00:00'))
        config = replace(self.config, repeat_rules=[rule])
        self.assertFalse(materialize_daily(config, now))
        self.assertEqual(next_daily(config, now).strftime('%H:%M:%S'), '11:59:15')
        self.assertTrue(materialize_daily(config, now + timedelta(seconds=1)))
        goal = config.goals[0]
        self.assertEqual(goal.id, 'daily-weekly-rush-2026-10-26')
        self.assertEqual(goal.candidates[0].date, '2026-10-28')
        self.assertEqual(goal.rush.at, '2026-10-26T12:00:00+08:00')
        self.assertFalse(materialize_daily(config, now + timedelta(seconds=2)))

    # ==========================================
    # Function: Prepare after midnight releases on the preceding calendar day without changing execution weekdays.
    # Method: Choose Monday only and start its 45-second preparation on Sunday night.
    # ==========================================
    def test_cross_midnight_preparation(self):
        rule = RepeatRule('midnight', '午夜', [self.target], weekdays=[0], rush=replace(self.rush, at='00:00:10'))
        config = replace(self.config, repeat_rules=[rule])
        now = datetime(2026, 10, 25, 23, 59, 25, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertTrue(materialize_daily(config, now))
        self.assertTrue(config.goals[0].rush.at.startswith('2026-10-26T00:00:10'))

    # ==========================================
    # Function: Keep per-goal rush settings independent through next-day copies and repeat conversion.
    # Method: Exercise the production workspace model and round-trip its validated payload.
    # ==========================================
    def test_copy_and_template_conversion_preserve_rush(self):
        # This goal is future-dated relative to the actual editor's local day.
        tomorrow = datetime.now(ZoneInfo('Asia/Shanghai')).date() + timedelta(days=2)
        goal = replace(self.goal, candidates=[replace(self.target, date=tomorrow.isoformat())],
                       rush=replace(self.rush, at=tomorrow.isoformat() + 'T12:00:00'))
        config = replace(self.config, goals=[goal])
        workspace = Workspace(config)
        copied = workspace.copy_next(goal.id)
        self.assertEqual(copied['rush']['at'][:10], (tomorrow + timedelta(days=1)).isoformat())
        workspace.toggle_repeat()
        self.assertEqual(workspace.get()['rush']['at'], '12:00:00')
        workspace.toggle_repeat()
        self.assertIn('T12:00:00', workspace.get()['rush']['at'])
        parsed = config_from_dict(workspace.payload())
        self.assertTrue(all(g.rush.enabled for g in parsed.goals))

    # ==========================================
    # Function: Allow one goal to opt out of another goal's server-clock correction.
    # Method: Apply a shared estimated offset and compare enabled/disabled clock-sync deadlines.
    # ==========================================
    def test_clock_choice_and_manual_compensation_are_per_goal(self):
        scheduler = RushCoordinator(self.config, self.pacer)
        scheduler.clock.anchor_epoch += 2
        local = scheduler.deadline(self.goal)
        remote = scheduler.deadline(replace(self.goal, rush=replace(self.rush, sync_clock=True)))
        self.assertAlmostEqual(local - remote, 2)
        compensated = scheduler.deadline(replace(self.goal, rush=replace(self.rush, manual_offset_ms=100)))
        self.assertAlmostEqual(local - compensated, .1)
