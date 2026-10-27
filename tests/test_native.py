"""Actual Tk widget tests; no HTTP GUI, live login, or real orders."""

from dataclasses import asdict
from datetime import date, timedelta
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from autoreserve.catalog import ANY_COURT
from autoreserve.config import load_config, save_config
from autoreserve.desktop_controller import DesktopController, context_key
from autoreserve.state import private_json
from test_catalog import directory_fixture, native_config


# ==========================================
# Function: Verify the real native client without browser rendering or network work.
# Method: Use isolated plans, cached metadata, and actual Tk widgets under WSLg.
# ==========================================
@unittest.skipUnless(os.environ.get("RUN_NATIVE_TESTS") == "1", "设置 RUN_NATIVE_TESTS=1 启用原生窗口测试")
class NativeWidgetTests(unittest.TestCase):
    # ==========================================
    # Function: Open an isolated real native window with a cached catalog.
    # Method: Preserve user files and make unexpected network/browser work fail.
    # ==========================================
    def setUp(self):
        import tkinter as tk
        from autoreserve.desktop import DesktopApp
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.config = native_config()
        save_config(self.config, self.path / "config.json")
        private_json(self.path / "auth/catalog.json", directory_fixture())
        private_json(self.path / "auth/courts.json", {context_key("学生中心", "健身房"): ["场地1", "场地2"]})
        self.launch = patch("autoreserve.desktop_controller.open_browser", side_effect=AssertionError("unexpected browser"))
        self.launch.start()
        self.controller = DesktopController(self.path / "config.json", self.path / "auth")
        self.root = tk.Tk()
        self.app = DesktopApp(self.root, self.controller)
        self.errors = []
        self.app.error = lambda exc: self.errors.append(str(exc))
        self.root.update()

    # ==========================================
    # Function: Release native widgets and test files after each scenario.
    # Method: Stop workers cooperatively and destroy the private Tk root.
    # ==========================================
    def tearDown(self):
        import tkinter as tk
        self.controller.stop_event.set()
        if self.controller.worker:
            self.controller.worker.join(timeout=2)
        try:
            for job in self.root.tk.call("after", "info"):
                self.root.after_cancel(job)
            self.root.destroy()
        except tk.TclError:
            pass
        self.launch.stop()
        self.temp.cleanup()

    # ==========================================
    # Function: Create and persist multiple dates and ranked slot alternatives.
    # Method: Operate real list selections, candidate moves, goal moves, and save callbacks.
    # ==========================================
    def test_native_multi_select_priority_and_save(self):
        app = self.app
        app.add_goal()
        app.goal_name.set("第二日期")
        day = (date.fromisoformat(self.config.goals[0].candidates[0].date) + timedelta(days=1)).isoformat()
        app.goal_date.set(day)
        app.court_list.selection_clear(0, "end")
        app.court_list.selection_set(1, 2)
        app.hour_list.selection_clear(0, "end")
        app.hour_list.selection_set(7, 8)
        app.add_candidates()
        self.assertEqual(len(app.goal()["candidates"]), 4)
        app.candidate_tree.selection_set("3")
        app.move_candidate(-1)
        app.move_candidate(-1)
        app.move_candidate(-1)
        app.move_goal(-1)
        self.assertTrue(app.save())
        saved = load_config(self.controller.config_path)
        self.assertEqual(saved.goals[0].name, "第二日期")
        self.assertEqual(saved.goals[0].candidates[0].court, "场地2")
        self.assertEqual(saved.goals[0].candidates[0].slots, ["15:00-16:00"])
        self.assertEqual(saved.goals[0].candidates[0].date, day)
        self.assertEqual(len(saved.goals), 2)
        self.assertEqual(self.errors, [])

    # ==========================================
    # Function: Use unrestricted courts before any inventory has been read.
    # Method: Select the default court and persist a new hourly alternative offline.
    # ==========================================
    def test_any_court_needs_no_live_scan(self):
        self.app.add_goal()
        self.app.add_candidates()
        self.assertEqual(self.app.goal()["candidates"][0]["court"], ANY_COURT)
        self.assertTrue(self.app.save())
        self.assertIsNone(self.controller.snapshot)
        self.assertIn("未读取空位", self.app.inventory_text.get())
        self.assertEqual(self.errors, [])

    # ==========================================
    # Function: Preserve unsaved editor text while monitoring status events arrive.
    # Method: Fail any full state read and update only the progress cells.
    # ==========================================
    def test_progress_keeps_editor_and_avoids_full_state_poll(self):
        self.app.goal_name.set("尚未保存的名称")
        self.app.interval.set("27")
        with patch.object(self.controller, "state", side_effect=AssertionError("full-state polling")):
            self.controller.progress({"message": "检查中", "goals": [{"id": "native-one", "status": "pending", "watch_state": "checking"}]})
            self.app.drain_events()
        self.assertEqual(self.app.goal_name.get(), "尚未保存的名称")
        self.assertEqual(self.app.interval.get(), "27")
        self.assertEqual(self.app.goal_tree.item("native-one", "values")[1], "检查中")
        self.assertTrue(self.app.dirty)

    # ==========================================
    # Function: Select cached projects without replacing the current cross-venue goal.
    # Method: Switch the builder to dance and back, checking scoped names and intact targets.
    # ==========================================
    def test_cached_project_switch_does_not_mix_courts(self):
        self.app.sport_var.set("舞蹈厅")
        self.app.sport_changed()
        self.assertEqual(self.app.data["goals"][0]["id"], "native-one")
        self.assertEqual(self.app.court_list.get(0, "end"), (ANY_COURT,))
        self.app.sport_var.set("健身房")
        self.app.sport_changed()
        self.assertEqual(self.app.data["goals"][0]["id"], "native-one")
        self.assertIn("场地1", self.app.court_list.get(0, "end"))
        self.assertEqual(self.errors, [])

    # ==========================================
    # Function: Keep the native event loop responsive while a worker is active.
    # Method: Count heartbeat callbacks during a simulated cooperative monitor wait.
    # ==========================================
    def test_background_monitor_does_not_block_tk(self):
        private_json(self.path / "auth/session.json", {"origin": "https://sports.sjtu.edu.cn", "values": {}})
        beats = []

        # ==========================================
        # Function: Simulate account work without touching a browser or live service.
        # Method: Wait on the same stop event, then publish normal worker completion.
        # ==========================================
        def worker(*args):
            self.controller.stop_event.wait(2)
            self.controller.busy, self.controller.operation = False, "idle"
            self.controller.emit("finished", None)

        # ==========================================
        # Function: Record independent native GUI event processing.
        # Method: Schedule short main-thread ticks while the worker waits.
        # ==========================================
        def heartbeat():
            beats.append(True)
            if len(beats) < 10:
                self.root.after(20, heartbeat)

        with patch.object(self.controller, "work", worker):
            self.app.action("run")
            self.root.after(10, heartbeat)
            self.root.after(250, self.controller.stop)
            self.root.after(450, self.root.quit)
            self.root.mainloop()
        self.assertGreaterEqual(len(beats), 8)
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.errors, [])

    # ==========================================
    # Function: Reject invalid bundled hour ranges before changing the plan.
    # Method: Select nonadjacent hours and require a native validation error.
    # ==========================================
    def test_nonconsecutive_bundle_is_rejected(self):
        before = asdict(load_config(self.controller.config_path))
        self.app.bundle.set(True)
        self.app.hour_list.selection_clear(0, "end")
        self.app.hour_list.selection_set(7)
        self.app.hour_list.selection_set(9)
        self.app.add_candidates()
        self.assertTrue(any("连续" in text for text in self.errors))
        self.assertEqual(asdict(load_config(self.controller.config_path)), before)

    # ==========================================
    # Function: Add several venues to one goal through the actual multi-select builder.
    # Method: Select two cached projects, add their chips, and persist both alternatives.
    # ==========================================
    def test_one_goal_contains_multiple_venues(self):
        self.app.add_goal()
        self.app.venue_var.set("学生中心 · 闵行")
        self.app.venue_changed()
        self.app.add_venue()
        self.app.venue_var.set("体育馆 · 徐汇")
        self.app.venue_changed()
        self.app.add_venue()
        self.app.add_candidates()
        targets = self.app.goal()["candidates"]
        self.assertEqual([t["context"]["venue"] for t in targets], ["学生中心", "体育馆"])
        self.assertEqual([t["context"]["sport"] for t in targets], ["健身房", "羽毛球"])
        self.assertTrue(self.app.save())
        saved = load_config(self.controller.config_path)
        self.assertEqual(len(saved.goals[-1].candidates), 2)
        self.assertEqual(self.errors, [])

    # ==========================================
    # Function: Notify immediately after a confirmed goal while other monitoring remains active.
    # Method: Process a real controller completion event and keep native heartbeat callbacks live.
    # ==========================================
    def test_completion_popup_is_nonblocking_and_deduplicated(self):
        self.controller.busy, self.controller.operation = True, "run"
        completed = {"id": "native-one", "name": "已完成目标", "status": "submitted",
                     "detail": {"order_id": "LOCAL-NOTICE", "venue": "学生中心", "sport": "健身房", "court": "场地1",
                                "date": self.app.goal_date.get(), "slots": ["14:00-15:00"]}}
        event = {"message": "订单已提交", "goals": [completed]}
        self.controller.progress(event)
        self.controller.progress(event)
        self.app.drain_events()
        self.root.update()
        self.assertIsNotNone(self.app.completion_window)
        self.assertTrue(self.app.completion_window.winfo_exists())
        self.assertEqual(len(self.app.completions), 1)
        self.assertIn("LOCAL-NOTICE", self.app.notice_text.get("1.0", "end"))
        heartbeats = []
        self.root.after(10, lambda: heartbeats.append(True))
        self.root.after(70, self.root.quit)
        self.root.mainloop()
        self.assertTrue(heartbeats)
        self.assertTrue(self.controller.busy)
        self.app.completion_window.destroy()
        self.assertFalse(self.controller.stop_event.is_set())

    # ==========================================
    # Function: Do not show success notifications for uncertain or rejected orders.
    # Method: Publish nonconfirmed outcomes through the production controller event path.
    # ==========================================
    def test_unknown_outcome_does_not_show_success_popup(self):
        for status in ("unknown", "rejected", "needs_verification"):
            self.controller.progress({"message": status, "goals": [{"id": "native-one", "status": status,
                                                                   "detail": {"order_id": "not-confirmed"}}]})
        self.app.drain_events()
        self.assertIsNone(self.app.completion_window)

    # ==========================================
    # Function: Keep essential controls visible in compact laptop-sized windows.
    # Method: Resize the actual native window and inspect mapped control coordinates.
    # ==========================================
    def test_compact_layout_keeps_login_and_start_visible(self):
        self.root.geometry("1120x780")
        self.root.update()
        for widget in (self.app.login_button, self.app.start_button):
            self.assertTrue(widget.winfo_ismapped())
            bottom = widget.winfo_rooty() - self.root.winfo_rooty() + widget.winfo_height()
            self.assertLessEqual(bottom, self.root.winfo_height())

    # ==========================================
    # Function: Keep goal selection and status updates free of synchronous configuration reads.
    # Method: Forbid loading files while interacting with the already-restored workspace.
    # ==========================================
    def test_selection_and_progress_do_not_read_config_files(self):
        with patch("autoreserve.desktop.load_config", side_effect=AssertionError("configuration read")):
            self.app.select_goal("native-one")
            self.app.sport_var.set("舞蹈厅")
            self.app.sport_changed()
            self.controller.progress({"message": "检查中", "goals": [{"id": "native-one", "status": "pending"}]})
            self.app.drain_events()
        self.assertEqual(self.errors, [])

    # ==========================================
    # Function: Reuse a completed goal as a fresh multi-date configuration.
    # Method: Keep its source ID/date intact and save copied priorities under independent IDs.
    # ==========================================
    def test_copy_completed_goal_and_persist_daily_template(self):
        app = self.app
        original = app.goal()["id"]
        app.goal_states[original] = {"status": "submitted"}
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        following = (date.today() + timedelta(days=2)).isoformat()
        copied = app.copy_to_dates([tomorrow, following])
        self.assertEqual(len(copied), 2)
        self.assertEqual(app.data["goals"][0]["id"], original)
        rule = app.set_daily_rule(2, "12:00")
        self.assertTrue(app.save())
        config = load_config(self.controller.config_path)
        self.assertEqual(config.repeat_rules[0].id, rule.id)
        self.assertEqual(len(config.goals), 3)
        self.assertIsNone(self.controller.snapshot)
        self.assertEqual(self.errors, [])

    # ==========================================
    # Function: Keep the event loop responsive while a deliberately slow disk save runs.
    # Method: Block only the save worker and assert a main-thread heartbeat and eventual completion.
    # ==========================================
    def test_slow_save_runs_in_background(self):
        beats = []
        original = self.controller.save

        # ==========================================
        # Function: Simulate slow filesystem persistence without altering actual protection logic.
        # Method: Wait on the worker before performing the real isolated save.
        # ==========================================
        def slow_save(data):
            threading.Event().wait(0.2)
            original(data)

        with patch.object(self.controller, "save", side_effect=slow_save):
            self.app.goal_name.set("后台保存")
            self.assertTrue(self.app.save(background=True))
            self.root.after(20, lambda: beats.append(True))
            self.root.after(400, self.root.quit)
            self.root.mainloop()
            self.app.save_worker.join(2)
        self.assertTrue(beats)
        self.assertFalse(self.app.saving)
        self.assertFalse(self.app.dirty)
        self.assertEqual(load_config(self.controller.config_path).goals[0].name, "后台保存")
        self.assertEqual(self.errors, [])

    # ==========================================
    # Function: Deliver worker events without relying on a polling timer.
    # Method: Cancel the fallback timer and require the native pipe event to update the window.
    # ==========================================
    def test_pipe_wakes_gui_without_poll_timer(self):
        self.root.after_cancel(self.app.after_id)
        self.app.after_id = None
        worker = threading.Thread(target=lambda: self.controller.emit("auth", "即时事件"))
        worker.start()
        worker.join(1)
        self.root.update()
        self.assertEqual(self.app.auth_text.get(), "即时事件")

    # ==========================================
    # Function: Avoid repeatedly repainting unchanged controls or clearing court selections.
    # Method: Reapply idle state and unchanged catalog choices with real selected native rows.
    # ==========================================
    def test_unchanged_controls_skip_redraw_and_preserve_selection(self):
        self.app.court_list.selection_clear(0, "end")
        self.app.court_list.selection_set(1)
        with patch.object(self.app.start_button, "draw") as draw:
            for _ in range(5):
                self.app.set_busy()
                self.app.refresh_courts()
            draw.assert_not_called()
        self.assertEqual(self.app.court_list.curselection(), (1,))

    # ==========================================
    # Function: Convert a target into its own editable weekly template and persist it.
    # Method: Use real mode controls, weekdays, pause states, and a complete saved round trip.
    # ==========================================
    def test_inline_weekly_mode_converts_without_extra_booking(self):
        app = self.app
        app.toggle_repeat()
        self.assertTrue(app.is_repeat())
        self.assertEqual(len(app.data['goals']), 0)
        self.assertEqual(len(app.data['repeat_rules']), 1)
        for day in [1, 3, 5, 6]:
            app.toggle_weekday(day)
        app.repeat_at.set('11:30')
        app.repeat_ahead.set('3')
        app.schedule_changed()
        self.assertEqual(app.goal()['weekdays'], [0, 2, 4])
        self.assertEqual(app.workspace_card.cget('highlightbackground'), '#2563eb')
        self.assertEqual(str(app.date_entry.cget('state')), 'disabled')
        self.assertTrue(app.save())
        saved = load_config(self.controller.config_path)
        self.assertEqual(saved.goals, [])
        self.assertEqual(saved.repeat_rules[0].at, '11:30')
        self.assertEqual(saved.repeat_rules[0].days_ahead, 3)
        self.assertEqual(saved.repeat_rules[0].weekdays, [0, 2, 4])
        app.repeat_enabled.set(False)
        app.schedule_changed()
        self.assertEqual(app.workspace_card.cget('highlightbackground'), '#e9e9e7')
        self.assertEqual(self.errors, [])

    # ==========================================
    # Function: Copy the source date's next day through the inline plus action.
    # Method: Use a date far beyond tomorrow and verify independent nested alternatives.
    # ==========================================
    def test_inline_plus_uses_source_date_and_clears_recurrence(self):
        app = self.app
        source = app.goal()
        source_day = (date.today() + timedelta(days=5)).isoformat()
        app.goal_date.set(source_day)
        copies = app.copy_next_day(source['id'])
        self.assertEqual(copies[0]['candidates'][0]['date'], (date.fromisoformat(source_day)+timedelta(days=1)).isoformat())
        self.assertEqual(source['candidates'][0]['date'], source_day)
        self.assertNotEqual(copies[0]['id'], source['id'])
        app.toggle_repeat()
        rule_id = app.selected_goal
        copied = app.copy_next_day(rule_id)[0]
        self.assertIsNone(copied['repeat_id'])
        self.assertFalse(app.is_repeat())
        self.assertEqual(len(app.data['repeat_rules']), 1)
        self.assertEqual(self.errors, [])

    # ==========================================
    # Function: Keep repeat controls isolated between independently selected goals.
    # Method: Edit two templates, navigate back, and compare saved per-goal schedules.
    # ==========================================
    def test_weekly_controls_are_per_goal_and_roundtrip_to_ordinary(self):
        app = self.app
        app.toggle_repeat()
        first = app.selected_goal
        app.repeat_at.set('10:00')
        app.toggle_weekday(6)
        app.copy_next_day(first)
        app.toggle_repeat()
        second = app.selected_goal
        app.repeat_at.set('18:00')
        app.commit_editor()
        app.select_goal(first)
        self.assertEqual(app.repeat_at.get(), '10:00')
        self.assertFalse(app.week_vars[6].get())
        app.select_goal(second)
        self.assertEqual(app.repeat_at.get(), '18:00')
        self.assertTrue(app.week_vars[6].get())
        app.toggle_repeat()
        self.assertFalse(app.is_repeat())
        self.assertEqual(len(app.data['repeat_rules']), 1)
        self.assertEqual(len(app.data['goals']), 1)
        self.assertTrue(app.save())
        self.assertEqual(self.errors, [])

    # ==========================================
    # Function: Keep the last weekday selected and prevent edits while monitoring.
    # Method: Attempt an empty week and repeat the controls with the real busy guard enabled.
    # ==========================================
    def test_weekday_minimum_and_busy_guard(self):
        app = self.app
        app.toggle_repeat()
        for day in range(1, 7):
            app.toggle_weekday(day)
        app.toggle_weekday(0)
        self.assertEqual(app.goal()['weekdays'], [0])
        app.commit_editor()
        before = asdict(load_config(self.controller.config_path))
        self.controller.busy, self.controller.operation = True, 'run'
        app.toggle_weekday(1)
        app.copy_next_day(app.selected_goal)
        app.delete_goal(app.selected_goal)
        self.assertEqual(app.goal()['weekdays'], [0])
        self.assertEqual(len(app.data['repeat_rules']), 1)
        self.assertEqual(asdict(load_config(self.controller.config_path)), before)
        self.controller.busy = False
        self.assertEqual(self.errors, [])

    # ==========================================
    # Function: Keep added venues visible and retain each target's unfinished selections.
    # Method: Add three distinct contexts, reflow at laptop width, then navigate away and back.
    # ==========================================
    def test_venue_cards_wrap_and_drafts_survive_navigation(self):
        app = self.app
        original = app.selected_goal
        app.add_venue()
        app.sport_var.set('舞蹈厅')
        app.sport_changed()
        app.add_venue()
        app.venue_var.set('体育馆 · 徐汇')
        app.venue_changed()
        app.add_venue()
        self.root.geometry('1120x780')
        self.root.update()
        cards = app.venue_tags.winfo_children()
        self.assertEqual(len(cards), 3)
        self.assertGreater(len({int(c.grid_info()['row']) for c in cards}), 1)
        for card in cards:
            self.assertLessEqual(card.winfo_x()+card.winfo_width(), app.venue_tags.winfo_width())
        app.add_goal()
        self.assertEqual(app.builder_contexts, [])
        app.select_goal(original)
        self.assertEqual(len(app.builder_contexts), 3)
        self.assertEqual(len(app.venue_tags.winfo_children()), 3)
        self.assertEqual(self.errors, [])

    # ==========================================
    # Function: Make wildcard and specific court selection mutually exclusive.
    # Method: Toggle actual native tiles and inspect selected indices and strong blue feedback.
    # ==========================================
    def test_selection_tiles_have_explicit_blue_state(self):
        choices = self.app.court_list
        choices.toggle(1)
        self.assertEqual(choices.curselection(), (1,))
        self.assertEqual(choices.tiles[1].cget('bg'), '#2563eb')
        self.assertTrue(choices.tiles[1].cget('text').startswith('✓'))
        choices.toggle(0)
        self.assertEqual(choices.curselection(), (0,))
        choices.configure(state='disabled')
        choices.toggle(1)
        self.assertEqual(choices.curselection(), (0,))

    # ==========================================
    # Function: Expose retained rule-only workspaces after reopening the client.
    # Method: Save a converted template and reconstruct a second real desktop from its config.
    # ==========================================
    def test_reopen_rule_only_workspace_selects_repeat(self):
        import tkinter as tk
        from autoreserve.desktop import DesktopApp
        self.app.toggle_repeat()
        self.assertTrue(self.app.save())
        second = tk.Tk()
        try:
            controller = DesktopController(self.path/'config.json', self.path/'auth')
            other = DesktopApp(second, controller, auto_catalog=False)
            self.root.update()
            self.assertTrue(other.is_repeat())
            self.assertIn('重复目标', other.mode_text.get())
            self.assertEqual(other.workspace_card.cget('highlightbackground'), '#2563eb')
            second.after_cancel(other.after_id)
        finally:
            second.destroy()

    # ==========================================
    # Function: Verify decoration removal targets the Tk client wrapper rather than the WM frame.
    # Method: Read the real X11 Motif property and retain ordinary managed-window behavior.
    # ==========================================
    def test_chrome_hints_are_on_managed_client_wrapper(self):
        import ctypes as C
        chrome = self.app.chrome
        self.assertFalse(self.root.overrideredirect())
        self.assertEqual(len(chrome.grips), 8)
        width, height = self.root.winfo_width(), self.root.winfo_height()
        for edge, (x, y) in {'w': (2, height//2), 'e': (width-2, height//2),
                             'n': (width//2, 2), 's': (width//2, height-2),
                             'nw': (2, 2), 'ne': (width-2, 2),
                             'sw': (2, height-2), 'se': (width-2, height-2)}.items():
            with self.subTest(edge=edge):
                self.assertEqual(self.root.winfo_containing(self.root.winfo_rootx()+x, self.root.winfo_rooty()+y), chrome.grips[edge])
        self.assertEqual(self.root.title(), 'AutoReserve')
        lib = C.CDLL('libX11.so.6')
        lib.XOpenDisplay.argtypes, lib.XOpenDisplay.restype = [C.c_char_p], C.c_void_p
        lib.XInternAtom.argtypes, lib.XInternAtom.restype = [C.c_void_p, C.c_char_p, C.c_int], C.c_ulong
        lib.XGetWindowProperty.argtypes = [C.c_void_p, C.c_ulong, C.c_ulong, C.c_long, C.c_long,
                                          C.c_int, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int),
                                          C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.c_void_p)]
        lib.XFree.argtypes, lib.XCloseDisplay.argtypes = [C.c_void_p], [C.c_void_p]
        display = lib.XOpenDisplay(None)
        payload = C.c_void_p()
        try:
            atom = lib.XInternAtom(display, b'_MOTIF_WM_HINTS', False)
            actual, count, after, bits = C.c_ulong(), C.c_ulong(), C.c_ulong(), C.c_int()
            code = lib.XGetWindowProperty(display, chrome.x11_client, atom, 0, 5, False, atom,
                                           C.byref(actual), C.byref(bits), C.byref(count), C.byref(after), C.byref(payload))
            self.assertEqual(code, 0)
            self.assertEqual(count.value, 5)
            hints = C.cast(payload, C.POINTER(C.c_ulong))
            self.assertEqual([hints[i] for i in range(5)], [2, 0, 0, 0, 0])
        finally:
            if payload:
                lib.XFree(payload)
            lib.XCloseDisplay(display)

    # ==========================================
    # Function: Resize the real native root through a corner grip and verify restoration controls.
    # Method: Deliver actual pointer events then use the ordinary window manager minimize/map path.
    # ==========================================
    def test_native_corner_resize_and_minimize_restore(self):
        from types import SimpleNamespace
        app = self.app
        self.root.geometry('1200x800+100+100')
        self.root.update()
        grip = app.chrome.grips['se']
        start = SimpleNamespace(widget=grip, x_root=500, y_root=500)
        app.chrome.begin(start, 'se')
        app.chrome.end(SimpleNamespace(widget=grip, x_root=580, y_root=560))
        self.root.update()
        self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (1280, 860))
        self.assertIsNone(self.root.grab_current())
        app.chrome.minimize()
        self.root.update()
        self.assertIn(self.root.state(), ('iconic', 'normal'))
        self.root.deiconify()
        self.root.update()
        self.assertTrue(self.root.winfo_ismapped())
