"""Multi-venue schema, migration, legacy identity, and completion notification invariants."""

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from autoreserve.catalog import booking_url
from autoreserve.config import BookingContext, Config, Goal, Target, config_from_dict, load_config, save_config
from autoreserve.desktop_controller import DesktopController
from autoreserve.state import Journal
from test_catalog import native_config


# ==========================================
# Function: Verify multi-venue goals retain precise context and old submission identities.
# Method: Exercise the real config/controller/journal on temporary files only.
# ==========================================
class WorkspaceTests(unittest.TestCase):
    # ==========================================
    # Function: Permit identical dates/courts in different venues while rejecting exact duplicates.
    # Method: Round trip nested contexts and require distinct physical alternatives.
    # ==========================================
    def test_candidate_context_roundtrip_and_duplicate_validation(self):
        config = native_config()
        a = BookingContext(booking_url("a", "健身房"), "A馆", "健身房")
        b = BookingContext(booking_url("b", "羽毛球"), "B馆", "羽毛球")
        target = config.goals[0].candidates[0]
        goal = Goal("multi", "多馆", [replace(target, context=a), replace(target, context=b)])
        config.goals = [goal]
        restored = config_from_dict(asdict(config))
        self.assertEqual(restored.goals[0], goal)
        self.assertEqual(config.for_target(goal.candidates[1]).venue, "B馆")
        with self.assertRaisesRegex(ValueError, "重复"):
            Goal("duplicate", "重复", [replace(target, context=a), replace(target, context=a)])

    # ==========================================
    # Function: Keep legacy single-task and goal hashes unchanged by new optional fields.
    # Method: Compare against the original v0.4 serialized identity formulas.
    # ==========================================
    def test_legacy_identity_hashes_are_preserved(self):
        config = native_config()
        goal, target = config.goals[0], config.goals[0].candidates[0]
        config.targets = [target]
        original_target = {"date": target.date, "court": target.court, "slots": target.slots}
        task = [config.booking_url, config.venue, config.sport, [original_target]]
        expected = hashlib.sha256(json.dumps(task, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.assertEqual(config.task_key(), expected)
        original_goal = [config.booking_url, config.venue, config.sport, "goal-v1", goal.id]
        expected_goal = hashlib.sha256(json.dumps(original_goal, ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(config.goal_key(goal), expected_goal)
        origin = BookingContext(config.booking_url, config.venue, config.sport)
        scoped = replace(goal, origin=origin)
        self.assertEqual(replace(config, venue="其他", sport="羽毛球").goal_key(scoped), expected_goal)

    # ==========================================
    # Function: Merge old project profiles once without losing completed progress or slot claims.
    # Method: Emulate pre-canonical slot records, upgrade, then attempt a route-equivalent booking.
    # ==========================================
    def test_profiles_merge_preserves_legacy_orders_and_route_aliases(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a = native_config()
            b = replace(a, booking_url="https://sports.sjtu.edu.cn/pc/#/apointmentDetails/1/stadium/全部/0",
                        venue="体育馆", sport="羽毛球", goals=[replace(a.goals[0], id="b-goal", name="B馆目标")])
            save_config(a, root / "config.json")
            save_config(b, root / "auth/plans/b.json")
            journal = Journal(root / "auth/journal.sqlite3")
            try:
                goal = b.goals[0]
                target = replace(goal.candidates[0], court="场地1")
                key = b.goal_key(goal)
                journal.arm(replace(b.for_goal(goal), targets=[target]), target, key)
                journal.update(key, "submitted", {"order_id": "LEGACY-B"})
                for alias in b.slot_keys(target)[len(target.slots):]:
                    journal.db.execute("DELETE FROM slots WHERE slot_key=?", (alias,))
                journal.db.commit()
            finally:
                journal.close()
            controller = DesktopController(root / "config.json", root / "auth")
            upgraded = load_config(root / "config.json")
            self.assertEqual(upgraded.workspace_version, 2)
            self.assertEqual(len(upgraded.goals), 2)
            restored = next(g for g in upgraded.goals if g.id == "b-goal")
            self.assertEqual(upgraded.goal_key(restored), key)
            self.assertTrue((root / "auth/workspace-v1.json").exists())
            self.assertEqual(next(g for g in controller.state()["goals"] if g["id"] == "b-goal")["status"], "submitted")
            journal = Journal(root / "auth/journal.sqlite3")
            try:
                canonical = BookingContext(booking_url("stadium", "羽毛球"), "体育馆", "羽毛球")
                again = replace(target, context=canonical)
                with self.assertRaisesRegex(RuntimeError, "曾提交"):
                    journal.ensure_new(upgraded.for_target(again), "different-goal")
            finally:
                journal.close()
            again = DesktopController(root / "config.json", root / "auth")
            self.assertEqual(len(load_config(again.config_path).goals), 2)

    # ==========================================
    # Function: Validate fully scoped goals without a manually captured global booking page.
    # Method: Require explicit safe context for every target and reject foreign URLs.
    # ==========================================
    def test_explicit_context_does_not_need_global_venue(self):
        source = native_config()
        context = BookingContext(source.booking_url, source.venue, source.sport)
        goal = replace(source.goals[0], candidates=[replace(source.goals[0].candidates[0], context=context)])
        Config(goals=[goal]).validate(require_targets=True)
        unsafe = replace(context, booking_url="https://example.com/pc/#/apointmentDetails/1/a/b/0")
        with self.assertRaises(ValueError):
            Config(goals=[replace(goal, candidates=[replace(goal.candidates[0], context=unsafe)])]).validate(require_targets=True)

    # ==========================================
    # Function: Emit completion exactly once and never replay old success notifications on startup.
    # Method: Use durable completed records and duplicate/new/unknown progress events.
    # ==========================================
    def test_completion_event_requires_new_confirmed_order(self):
        with tempfile.TemporaryDirectory() as d:
            root, config = Path(d), native_config()
            save_config(config, root / "config.json")
            goal, target = config.goals[0], config.goals[0].candidates[0]
            journal = Journal(root / "auth/journal.sqlite3")
            journal.arm(config.for_goal(goal), target, config.goal_key(goal))
            journal.update(config.goal_key(goal), "submitted", {"order_id": "OLD"})
            journal.close()
            controller = DesktopController(root / "config.json", root / "auth")
            old = {"id": goal.id, "status": "submitted", "detail": {"order_id": "OLD"}}
            fresh = {"id": "new-goal", "name": "新目标", "status": "submitted", "detail": {"order_id": "NEW"}}
            uncertain = {"id": "uncertain", "status": "unknown", "detail": {"order_id": "MAYBE"}}
            for state in (old, fresh, fresh, uncertain):
                controller.progress({"message": "进度", "goals": [state]})
            notices = []
            while not controller.events.empty():
                kind, value = controller.events.get_nowait()
                if kind == "goal_completed":
                    notices.append(value)
            self.assertEqual([n["id"] for n in notices], ["new-goal"])

    # ==========================================
    # Function: Refuse changes that would bypass a recorded goal's durable identity.
    # Method: Edit only its origin metadata and require the shared save guard to reject it.
    # ==========================================
    def test_recorded_goal_origin_cannot_be_rewritten(self):
        with tempfile.TemporaryDirectory() as d:
            root, config = Path(d), native_config()
            save_config(config, root / "config.json")
            controller = DesktopController(root / "config.json", root / "auth")
            current = load_config(controller.config_path)
            goal, target = current.goals[0], current.goals[0].candidates[0]
            journal = Journal(root / "auth/journal.sqlite3")
            journal.arm(current.for_target(target), target, current.goal_key(goal))
            journal.close()
            data = asdict(current)
            data["goals"][0]["origin"]["venue"] = "另一场馆"
            with self.assertRaisesRegex(ValueError, "身份不能修改"):
                controller.save(data)
