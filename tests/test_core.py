"""Meaningful state-machine invariants using the Python standard library."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from autoreserve.config import Config, Goal, Target, config_from_dict, load_config, parse_start, save_config
from autoreserve.runner import classify_response, wait_for_start
from autoreserve.sjtu import candidate_cells, validate_grid, verify_selection
from autoreserve.state import Journal, account_lock, private_json


# ==========================================
# Function: Create a future candidate independent of the test date.
# Method: Use tomorrow in the same timezone as the user.
# ==========================================
def target(court="场地1", slots=None):
    date = (datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=1)).isoformat()
    return Target(date, court, slots or ["14:00-15:00"])


# ==========================================
# Function: Build a realistic normalized grid snapshot.
# Method: Expose 15 hourly rows and two exact court columns.
# ==========================================
def snapshot():
    rows = [["available", "unavailable"] for _ in range(15)]
    rows[7][0] = "selected"
    return {"venue": "测试馆", "sport": "健身房", "active_date": target().date,
            "loading": False, "columns": ["场地1", "场地2"],
            "rows": rows,
            "times": [f"{h:02d}:00" for h in range(7, 23)],
            "summary": f"{target().date} 场地1 14:00-15:00", "selected_count": 1,
            "total": "实际金额：￥0元"}


# ==========================================
# Function: Verify configuration, matching, price, and response invariants.
# Method: Exercise ambiguous and invalid inputs as well as valid cases.
# ==========================================
class CoreTests(unittest.TestCase):
    # ==========================================
    # Function: Verify strict configuration serialization.
    # Method: Round trip a complete config and reject unknown keys.
    # ==========================================
    def test_config_roundtrip_and_unknown_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = Config(targets=[target()])
            save_config(config, path)
            self.assertEqual(config, load_config(path))
            data = json.loads(path.read_text())
            data["typo_interval"] = 0
            path.write_text(json.dumps(data))
            with self.assertRaises(TypeError):
                load_config(path)

    # ==========================================
    # Function: Reject bookings unsupported by the actual hourly grid.
    # Method: Check duplicates, nonconsecutive hours, and partial-hour ranges.
    # ==========================================
    def test_reject_invalid_slots(self):
        for slots in (["14:30-15:30"], ["14:00-15:00"] * 2, ["14:00-15:00", "16:00-17:00"], []):
            with self.subTest(slots=slots), self.assertRaises(ValueError):
                Target(target().date, "场地1", slots)

    # ==========================================
    # Function: Ensure dates have full calendar-year identity.
    # Method: Compare a December/January transition in the configured timezone.
    # ==========================================
    def test_timezones_and_year_boundary(self):
        stamp = parse_start("2026-12-31T23:59:59", "Asia/Shanghai")
        self.assertEqual(stamp.utcoffset(), timedelta(hours=8))
        self.assertEqual((stamp + timedelta(seconds=1)).date().isoformat(), "2027-01-01")
        self.assertEqual(parse_start("2027-01-01T00:00:00+09:00", "Asia/Shanghai").utcoffset(), timedelta(hours=9))
        with self.assertRaises(ValueError):
            parse_start("12:00:00", "Asia/Shanghai")

    # ==========================================
    # Function: Prevent scheduled checks from running before release time.
    # Method: Drive a fake clock forward and verify bounded sleeps until the target.
    # ==========================================
    def test_scheduled_wait(self):
        config = Config(start_at="1970-01-01T00:01:50+00:00")
        with patch("autoreserve.runner.time.time", side_effect=[100, 100, 100.1, 110]), \
             patch("autoreserve.runner.time.sleep") as sleep:
            wait_for_start(config)
            self.assertEqual(sleep.call_count, 2)
            self.assertTrue(all(call.args[0] <= 0.25 for call in sleep.call_args_list))

    # ==========================================
    # Function: Reject unsafe or mistyped operational settings.
    # Method: Exercise origin restrictions and finite timing limits.
    # ==========================================
    def test_limits_and_origin(self):
        for update in ({"poll_interval_seconds": 0}, {"headless": "false"},
                       {"max_total_price": "NaN"}, {"max_poll_seconds": float("inf")},
                       {"booking_url": "https://sports.sjtu.edu.cn.evil.example/pc/"},
                       {"booking_url": "http://127.0.0.1:8765/"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                replace(Config(), **update).validate()

    # ==========================================
    # Function: Verify that unavailable cells and wrong dates cannot match.
    # Method: Match exact row/column indices under a validated layout.
    # ==========================================
    def test_candidate_matching(self):
        shot = snapshot()
        shot["rows"][7][0] = "available"
        self.assertEqual(candidate_cells(shot, target()), [(7, 0)])
        self.assertIsNone(candidate_cells(shot, target("场地2")))
        shot["active_date"] = "2027-12-31"
        self.assertIsNone(candidate_cells(shot, target()))

    # ==========================================
    # Function: Treat closed dates as no availability rather than a broken grid.
    # Method: Accept a genuinely empty loaded table and keep monitoring.
    # ==========================================
    def test_empty_closed_date(self):
        self.assertIsNone(candidate_cells(snapshot() | {"columns": [], "rows": []}, target()))

    # ==========================================
    # Function: Stop on a changed time axis or grid shape.
    # Method: Reject shifted labels and inconsistent column counts.
    # ==========================================
    def test_grid_change_stops(self):
        shot = snapshot()
        shot["times"][0] = "06:00"
        with self.assertRaises(RuntimeError):
            validate_grid(shot)
        shot = snapshot()
        shot["rows"][0].pop()
        with self.assertRaises(RuntimeError):
            validate_grid(shot)

    # ==========================================
    # Function: Require exact summary and price agreement.
    # Method: Reject additional cells, shifted time, wrong venue, and over-budget orders.
    # ==========================================
    def test_selection_integrity(self):
        config = Config(venue="测试馆",max_total_price="5.00")
        verify_selection(snapshot(), config, target())
        for update in ({"selected_count": 2}, {"venue": "另一个馆"},
                       {"total": "实际金额：￥10元"}, {"total": "未知金额"},
                       {"summary": "场地1 15:00-16:00"}):
            with self.subTest(update=update), self.assertRaises(RuntimeError):
                verify_selection(snapshot() | update, config, target())

    # ==========================================
    # Function: Interpret zero monitoring duration and zero monetary cap as explicit unlimited sentinels.
    # Method: Check finite config persistence, runtime deadlines and price recognition independently.
    # ==========================================
    def test_zero_limits_are_unlimited(self):
        config=Config(venue='测试馆',max_total_price='0.0')
        self.assertEqual(config.max_poll_seconds,0.0)
        config.validate()
        self.assertEqual(config.monitor_deadline(100),float('inf'))
        self.assertEqual(replace(config,max_poll_seconds=60).monitor_deadline(100),160)
        verify_selection(snapshot()|{'total':'实际金额：￥150元'},config,target())
        with self.assertRaises(RuntimeError):
            verify_selection(snapshot()|{'total':'未知金额'},config,target())
        with self.assertRaises(ValueError):
            replace(config,max_poll_seconds=-1).validate()

    # ==========================================
    # Function: Avoid assuming success from a click or incomplete response.
    # Method: Require code zero and a nonempty scalar order ID.
    # ==========================================
    def test_order_classification(self):
        self.assertEqual(classify_response({"code": 0, "data": "123"})["status"], "submitted")
        self.assertEqual(classify_response({"code": 1002})["status"], "needs_verification")
        self.assertEqual(classify_response({"code": 500})["status"], "rejected")
        for payload in (None, {"code": False, "data": "123"}, {"code": 0, "data": None},
                        {"code": 0, "data": {}}, {"code": 0, "data": True}):
            with self.subTest(payload=payload):
                self.assertEqual(classify_response(payload)["status"], "unknown")

    # ==========================================
    # Function: Keep interrupted submission latches across process restarts.
    # Method: Reopen SQLite and verify the task remains blocked.
    # ==========================================
    def test_latch_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            config = Config(targets=[target()])
            journal = Journal(path)
            journal.arm(config, config.targets[0])
            journal.close()
            journal = Journal(path)
            try:
                with self.assertRaises(RuntimeError):
                    journal.ensure_new(config)
                self.assertEqual(journal.entries()[0]["status"], "submit_intent")
            finally:
                journal.close()

    # ==========================================
    # Function: Prevent duplicate hours after configuration changes.
    # Method: Overlap a claimed individual slot in a different candidate group.
    # ==========================================
    def test_overlap_blocks_changed_config(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "journal.sqlite3")
            config = Config(targets=[target()])
            try:
                journal.arm(config, config.targets[0])
                changed = replace(config, targets=[target(slots=["14:00-15:00", "15:00-16:00"])])
                with self.assertRaises(RuntimeError):
                    journal.ensure_new(changed)
            finally:
                journal.close()

    # ==========================================
    # Function: Keep account access exclusive and auth files private.
    # Method: Verify nested-lock exclusion on both platforms and POSIX mode bits where the OS supports them.
    # ==========================================
    def test_lock_and_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            with account_lock(directory):
                with self.assertRaises(RuntimeError):
                    with account_lock(directory):
                        pass
            path = Path(directory) / "auth.json"
            private_json(path, {"token": "local-test-only"})
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    # ==========================================
    # Function: Validate multi-date goals and preserve candidate priority.
    # Method: Round trip nested goals and reject duplicate identities or mixed dates.
    # ==========================================
    def test_multi_goal_config(self):
        first = target()
        alternate = target("场地2")
        goal = Goal("goal-one", "目标一", [alternate, first])
        from dataclasses import asdict
        config = Config(goals=[goal], monitor_until_complete=True)
        self.assertEqual(config_from_dict(asdict(config)), config)
        with self.assertRaises(ValueError):
            Goal("mixed", "混合日期", [first, replace(first, date="2099-01-01")])
        with self.assertRaises(ValueError):
            Config(goals=[goal, goal]).validate()

    # ==========================================
    # Function: Keep completed goals complete when priorities change.
    # Method: Confirm stable goal keys and unchanged independent slot identities.
    # ==========================================
    def test_goal_key_survives_priority_change(self):
        config = Config()
        goal = Goal("stable", "测试", [target(), target("场地2")])
        changed = replace(goal, name="新名称", candidates=list(reversed(goal.candidates)))
        self.assertEqual(config.goal_key(goal), config.goal_key(changed))
        self.assertNotEqual(config.goal_key(goal), config.goal_key(replace(goal, id="another")))

    # ==========================================
    # Function: Allow safe retries only after explicit rejection.
    # Method: Archive rejected attempts and retain unknown submission latches.
    # ==========================================
    def test_rejection_archive_preserves_unknown_latch(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "journal.sqlite3")
            config = Config(targets=[target()])
            try:
                journal.arm(config, target(), "goal-key")
                journal.update("goal-key", "unknown", {"message": "断网"})
                with self.assertRaises(RuntimeError):
                    journal.archive_rejection("goal-key")
                journal.update("goal-key", "rejected", {"message": "该场地已满"})
                journal.archive_rejection("goal-key")
                journal.ensure_new(config, "goal-key")
                self.assertEqual(journal.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
            finally:
                journal.close()


if __name__ == "__main__":
    unittest.main()
