"""Deterministic configuration reuse tests; no account or network access."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from autoreserve.config import Goal, RepeatRule, config_from_dict
from autoreserve.repeat import copy_goals, materialize_daily, next_daily
from test_catalog import native_config


# ==========================================
# Function: Verify daily scheduling and copying preserve independent order identities.
# Method: Use explicit local clocks, immutable templates, and isolated in-memory workspaces.
# ==========================================
class RepeatTests(unittest.TestCase):
    # ==========================================
    # Function: Build a fixed-clock daily rule using a valid existing venue configuration.
    # Method: Freeze the clock and retain template alternatives for comparison.
    # ==========================================
    def setUp(self):
        self.now = datetime(2026, 10, 26, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.config = native_config()
        self.rule = RepeatRule("gym", "每日健身", self.config.goals[0].candidates, 2, "12:00")
        self.config.repeat_rules = [self.rule]

    # ==========================================
    # Function: Avoid creating targets or reading inventory before a daily trigger.
    # Method: Compare an explicit pre-trigger clock with the computed next occurrence.
    # ==========================================
    def test_wait_until_local_trigger(self):
        before = self.now - timedelta(seconds=1)
        self.assertFalse(materialize_daily(self.config, before))
        self.assertEqual(next_daily(self.config, before), self.now)

    # ==========================================
    # Function: Generate one independent date occurrence and survive repeated restarts.
    # Method: Round-trip the complete JSON schema and repeat the same day's generation.
    # ==========================================
    def test_due_once_and_persists_identity(self):
        self.assertTrue(materialize_daily(self.config, self.now))
        goal = self.config.goals[-1]
        self.assertEqual(goal.candidates[0].date, "2026-10-28")
        self.assertEqual(goal.repeat_id, "gym")
        self.assertIsNotNone(goal.candidates[0].context)
        reopened = config_from_dict(asdict(self.config))
        self.assertFalse(materialize_daily(reopened, self.now + timedelta(hours=3)))
        self.assertEqual(reopened.goal_key(reopened.goals[-1]), self.config.goal_key(goal))
        self.assertEqual(next_daily(reopened, self.now), self.now + timedelta(days=1))

    # ==========================================
    # Function: Add tomorrow's occurrence alongside an earlier unfinished target.
    # Method: Advance the local day without clearing the prior rolling target.
    # ==========================================
    def test_new_day_keeps_unfinished_future_goals(self):
        materialize_daily(self.config, self.now)
        first = self.config.goals[-1]
        materialize_daily(self.config, self.now + timedelta(days=1))
        self.assertIn(first, self.config.goals)
        self.assertEqual([g.candidates[0].date for g in self.config.goals[-2:]], ["2026-10-28", "2026-10-29"])

    # ==========================================
    # Function: Retain uncertain order requirements while retiring old rolling UI rows.
    # Method: Prune only safe past occurrences and keep their durable journal externally intact.
    # ==========================================
    def test_unknown_is_kept_and_safe_old_row_is_pruned(self):
        self.config.goals = [Goal("old", "old", [replace(self.rule.candidates[0], date="2026-10-20")], repeat_id="gym")]
        record = {"status": "unknown"}
        materialize_daily(self.config, self.now, lambda g: record)
        self.assertEqual(self.config.goals[0].id, "old")
        record["status"] = "submitted"
        self.assertTrue(materialize_daily(self.config, self.now, lambda g: record))
        self.assertNotIn("old", [g.id for g in self.config.goals])

    # ==========================================
    # Function: Copy priority lists without copying progress or mutating the source.
    # Method: Check independent nested lists, fresh IDs, and stable source context.
    # ==========================================
    def test_copy_is_deep_and_independent(self):
        source = asdict(self.config.goals[0])
        source["repeat_id"] = "gym"
        before = deepcopy(source)
        copies = copy_goals(source, ["2026-10-29", "2026-10-30", "2026-10-29"])
        self.assertEqual(len(copies), 2)
        self.assertEqual(len({source["id"], *[g["id"] for g in copies]}), 3)
        self.assertTrue(all(g["repeat_id"] is None for g in copies))
        copies[0]["candidates"][0]["slots"].append("15:00-16:00")
        self.assertEqual(source, before)
        self.assertEqual(copies[1]["candidates"][0]["slots"], before["candidates"][0]["slots"])

    # ==========================================
    # Function: Reject invalid daily rules and allow a workspace consisting only of rules.
    # Method: Validate strict schema values and backward-compatible empty defaults.
    # ==========================================
    def test_validation_and_rule_only_workspace(self):
        self.config.goals = []
        self.config.validate(require_targets=True)
        for field, value in [("days_ahead", -1), ("days_ahead", 8), ("at", "24:00"), ("enabled", 1)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                RepeatRule(**(asdict(self.rule) | {"candidates": self.rule.candidates, field: value}))
        old = asdict(self.config)
        old.pop("repeat_rules")
        self.assertEqual(config_from_dict(old).repeat_rules, [])

    # ==========================================
    # Function: Honor timezone conversion and disabled rules without generating stale missed days.
    # Method: Express the trigger in UTC and enable only the current local-day occurrence.
    # ==========================================
    def test_timezone_disabled_and_no_historical_catchup(self):
        self.rule.enabled = False
        self.assertFalse(materialize_daily(self.config, self.now))
        self.assertIsNone(next_daily(self.config, self.now))
        self.rule.enabled = True
        later = self.now.astimezone(ZoneInfo("UTC")) + timedelta(days=5)
        materialize_daily(self.config, later)
        self.assertEqual(len([g for g in self.config.goals if g.repeat_id]), 1)
        self.assertEqual(self.config.goals[-1].candidates[0].date, "2026-11-02")

    # ==========================================
    # Function: Skip disabled weekdays and retain weekly restart deduplication.
    # Method: Generate Monday/Wednesday occurrences and advance across a skipped Tuesday.
    # ==========================================
    def test_selected_weekdays_skip_and_resume(self):
        self.rule.weekdays = [0, 2, 4]
        self.assertTrue(materialize_daily(self.config, self.now))
        tuesday = self.now + timedelta(days=1)
        self.assertFalse(materialize_daily(self.config, tuesday))
        self.assertEqual(next_daily(self.config, tuesday), self.now + timedelta(days=2))
        self.assertTrue(materialize_daily(self.config, self.now + timedelta(days=2)))
        self.assertEqual(self.config.goals[-1].candidates[0].date, '2026-10-30')
        reopened = config_from_dict(asdict(self.config))
        self.assertFalse(materialize_daily(reopened, self.now + timedelta(days=2, hours=1)))

    # ==========================================
    # Function: Wait a full week when a single weekday has already executed.
    # Method: Use Monday-only generation and compare the next trigger across Sunday midnight.
    # ==========================================
    def test_single_weekday_rolls_to_next_week(self):
        self.rule.weekdays = [0]
        materialize_daily(self.config, self.now)
        self.assertEqual(next_daily(self.config, self.now), self.now + timedelta(days=7))
        sunday = (self.now + timedelta(days=6)).replace(hour=23, minute=59)
        materialize_daily(self.config, sunday)  # Expired earlier occurrences may be pruned.
        self.assertFalse(any(g.id.endswith(sunday.date().isoformat()) for g in self.config.goals))
        self.assertEqual(next_daily(self.config, sunday), self.now + timedelta(days=7))

    # ==========================================
    # Function: Define weekdays by the execution date rather than the future booking date.
    # Method: Run a Monday trigger with a Wednesday booking and verify Tuesday stays disabled.
    # ==========================================
    def test_execution_weekday_timezone_and_offset(self):
        self.rule.weekdays = [0]
        utc = self.now.astimezone(ZoneInfo('UTC'))
        self.assertTrue(materialize_daily(self.config, utc))
        self.assertEqual(self.config.goals[-1].candidates[0].date, '2026-10-28')
        self.assertFalse(materialize_daily(self.config, utc + timedelta(days=1)))

    # ==========================================
    # Function: Keep older daily rules compatible and reject ambiguous weekday data.
    # Method: Round-trip missing weekday defaults and exercise invalid JSON values.
    # ==========================================
    def test_weekday_validation_and_legacy_defaults(self):
        old = asdict(self.config)
        old['repeat_rules'][0].pop('weekdays')
        self.assertEqual(config_from_dict(old).repeat_rules[0].weekdays, list(range(7)))
        for value in ([], [0, 0], [7], [-1], [True], ['1'], '0123456', None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '执行日'):
                RepeatRule(**(asdict(self.rule) | {'candidates': self.rule.candidates, 'weekdays': value}))

    # ==========================================
    # Function: Find the earliest enabled trigger among separate weekly goals.
    # Method: Give two rules different weekdays and verify paused rules cannot win the schedule.
    # ==========================================
    def test_independent_weekly_rules_choose_earliest(self):
        self.rule.weekdays = [4]
        other = replace(self.rule, id='other', weekdays=[2], at='09:00')
        self.config.repeat_rules.append(other)
        self.assertEqual(next_daily(self.config, self.now), (self.now + timedelta(days=2)).replace(hour=9))
        other.enabled = False
        self.assertEqual(next_daily(self.config, self.now), self.now + timedelta(days=4))
