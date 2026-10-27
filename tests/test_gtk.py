"""Production GTK widget regressions with local fixtures; no live browser or orders."""

import ctypes as C
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from autoreserve.catalog import ANY_COURT
from autoreserve.config import load_config,save_config
from autoreserve.desktop_controller import DesktopController,context_key
from autoreserve.state import private_json
from test_catalog import native_config,directory_fixture


# ==========================================
# Function: Validate the actual GTK application rather than its legacy Tk predecessor.
# Method: Use real native widgets, isolated files, and a browser-launch tripwire.
# ==========================================
@unittest.skipUnless(os.environ.get('RUN_GTK_TESTS')=='1','设置 RUN_GTK_TESTS=1 启用 GTK 实机测试')
class GtkWorkspaceTests(unittest.TestCase):
    # ==========================================
    # Function: Build an isolated production GTK desktop with static venue metadata.
    # Method: Preserve user files and fail any unexpected browser/network action.
    # ==========================================
    def setUp(self):
        from autoreserve.gtk_desktop import GtkDesktop
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)
        save_config(native_config(),self.path/'config.json')
        private_json(self.path/'auth/catalog.json',directory_fixture())
        private_json(self.path/'auth/courts.json',{context_key('学生中心','健身房'):['场地1','场地2','场地3']})
        self.no_browser=patch('autoreserve.desktop_controller.open_browser',side_effect=AssertionError('unexpected browser'))
        self.no_browser.start()
        self.controller=DesktopController(self.path/'config.json',self.path/'auth')
        self.app=GtkDesktop(self.controller,auto_catalog=False)
        self.g=self.app.g
        self.g.gtk_window_present(self.app.window)
        self.pump(.08)

    # ==========================================
    # Function: Process native events without starting another GTK application.
    # Method: Pump the GTK/GLib main context for a bounded real-time interval.
    # ==========================================
    def pump(self, seconds=.05):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            while self.g.gtk_events_pending():
                self.g.gtk_main_iteration_do(False)
            time.sleep(.002)

    # ==========================================
    # Function: Wait for a real native allocation condition without assuming one fixed frame delay.
    # Method: Pump GTK until its layout predicate succeeds or a bounded diagnostic deadline expires.
    # ==========================================
    def wait_layout(self, predicate, timeout=3):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            self.pump(.02)
            if predicate():
                return
        self.fail('GTK native layout did not settle before the deadline')

    # ==========================================
    # Function: Release native widgets and signal sources before removing fixture files.
    # Method: Stop workers, finish pending saves, destroy roots, and assert callback health.
    # ==========================================
    def tearDown(self):
        self.controller.stop_event.set()
        if self.controller.worker:
            self.controller.worker.join(2)
        if hasattr(self.app,'save_worker'):
            self.app.save_worker.join(2)
        self.app.destroy()
        self.pump(.06)
        self.no_browser.stop()
        self.temp.cleanup()
        self.assertEqual(self.g.signal_errors,[])

    # ==========================================
    # Function: Construct settings with correct decorations before any pointer interaction.
    # Method: Inspect the first map event and verify the native titlebar offers close only.
    # ==========================================
    def test_settings_first_frame_has_native_close_only_header(self):
        self.app.open_settings()
        window=self.app.settings_window
        self.assertTrue(self.g.gtk_window_get_titlebar(window))
        self.assertEqual(self.g.gtk_header_bar_get_decoration_layout(self.app.settings_header),b':close')
        self.assertFalse(self.g.gtk_window_get_resizable(window))
        self.pump()
        self.assertTrue(self.g.gtk_widget_get_mapped(window))
        self.assertEqual(self.app.errors,[])

    # ==========================================
    # Function: Keep record and inventory panels fixed below the editor at laptop size.
    # Method: Resize the actual window and compare native allocations while scrolling the editor.
    # ==========================================
    def test_monitor_panels_are_separate_and_fixed(self):
        g,app=self.g,self.app
        g.gtk_window_resize(app.window,1120,820)
        self.pump(.1)
        self.assertNotEqual(app.w['log_card'],app.w['inventory_card'])
        self.assertEqual(g.gtk_widget_get_parent(app.w['log_card']),app.w['monitoring'])
        self.assertEqual(g.gtk_widget_get_parent(app.w['inventory_card']),app.w['monitoring'])
        self.assertEqual(g.gtk_widget_get_parent(app.w['monitoring']),app.w['main'])
        self.assertLess(g.gtk_widget_get_allocated_height(app.w['monitoring']),260)
        self.assertLess(g.gtk_widget_get_allocated_width(app.w['sidebar']),320)
        adjustment=g.gtk_scrolled_window_get_vadjustment(app.w['editor_scroll'])
        before=(g.gtk_widget_get_allocated_height(app.w['log_card']),g.gtk_widget_get_allocated_height(app.w['inventory_card']))
        g.gtk_adjustment_set_value(adjustment,400)
        self.pump()
        self.assertEqual(before,(g.gtk_widget_get_allocated_height(app.w['log_card']),g.gtk_widget_get_allocated_height(app.w['inventory_card'])))
        self.assertTrue(g.gtk_widget_get_mapped(app.w['logs']))

    # ==========================================
    # Function: Prevent inverse/negative overscroll at the sidebar's upper boundary.
    # Method: Fill the sidebar, drag its native adjustment, and verify clamping and direction.
    # ==========================================
    def test_sidebar_native_adjustment_clamps_at_top(self):
        app,g=self.app,self.g
        source=deepcopy(app.model.get())
        for i in range(35):
            item=deepcopy(source);item['id']='scroll-'+str(i)
            app.model.data['goals'].append(item)
        app.render_sidebar()
        self.pump(.1)
        adjustment=g.gtk_scrolled_window_get_vadjustment(app.w['goal_scroll'])
        self.wait_layout(lambda:g.gtk_adjustment_get_upper(adjustment)>g.gtk_adjustment_get_page_size(adjustment))
        g.gtk_adjustment_set_value(adjustment,180)
        self.assertEqual(g.gtk_adjustment_get_value(adjustment),180)
        g.gtk_adjustment_set_value(adjustment,0)
        g.gtk_adjustment_set_value(adjustment,-150)
        self.assertEqual(g.gtk_adjustment_get_value(adjustment),0)
        app.render_sidebar()
        self.pump()
        self.assertEqual(g.gtk_adjustment_get_value(adjustment),0)

    # ==========================================
    # Function: Keep multi-selection, source-date copies, and weekly options functional in GTK.
    # Method: Activate native toggles and inline row buttons, then validate the persisted config.
    # ==========================================
    def test_native_selection_copy_and_weekly_save(self):
        app,g=self.app,self.g
        source=app.model.selected
        g.gtk_toggle_button_set_active(app.court_buttons['场地1'],True)
        self.assertEqual(app.selected_values(app.court_buttons),['场地1'])
        g.gtk_toggle_button_set_active(app.hour_buttons['15:00-16:00'],True)
        app.add_candidates()
        self.assertEqual(len(app.model.get()['candidates']),3)
        g.gtk_button_clicked(app.sidebar_rows[source][3])
        self.assertEqual(len(app.model.data['goals']),2)
        app.toggle_repeat()
        for day in [1,3,5,6]:
            g.gtk_toggle_button_set_active(app.week_buttons[day],False)
        self.assertEqual(app.model.get()['weekdays'],[0,2,4])
        self.assertTrue(g.gtk_style_context_has_class(g.gtk_widget_get_style_context(app.w['editor']),b'recurring'))
        app.save()
        app.save_worker.join(2)
        self.pump(.12)
        saved=load_config(self.controller.config_path)
        self.assertEqual(saved.repeat_rules[0].weekdays,[0,2,4])
        self.assertEqual(len(saved.goals),1)
        self.assertEqual(app.errors,[])

    # ==========================================
    # Function: Keep native scrolling independent from selection repaint events.
    # Method: Compare the same GtkAdjustment before and after court/time toggles.
    # ==========================================
    def test_slot_scroller_retains_scroll_position_on_selection(self):
        app,g=self.app,self.g
        adjustment=g.gtk_scrolled_window_get_vadjustment(app.hour_scroll)
        self.assertGreater(g.gtk_adjustment_get_upper(adjustment),g.gtk_adjustment_get_page_size(adjustment))
        g.gtk_adjustment_set_value(adjustment,80)
        g.gtk_toggle_button_set_active(app.hour_buttons['16:00-17:00'],True)
        self.pump()
        self.assertAlmostEqual(g.gtk_adjustment_get_value(adjustment),80,delta=1)
        self.assertTrue(g.gtk_widget_get_mapped(app.hour_scroll))

    # ==========================================
    # Function: Preserve per-goal unfinished venue drafts through navigation.
    # Method: Add two venue cards, switch goals, and return via native ListBox selection.
    # ==========================================
    def test_venue_drafts_survive_native_navigation(self):
        app,g=self.app,self.g
        identity=app.model.selected
        app.add_venue()
        g.gtk_combo_box_set_active_id(app.venue_combo,b'stadium')
        app.add_venue()
        self.assertEqual(len(app.model.drafts[identity]),2)
        app.add_goal()
        self.assertEqual(len(g.children(app.venue_flow)),0)
        g.gtk_list_box_select_row(app.w['goals'],app.sidebar_rows[identity][0])
        self.assertEqual(app.model.selected,identity)
        self.assertEqual(len(g.children(app.venue_flow)),2)

    # ==========================================
    # Function: Update logs and availability continuously without rebuilding the editor.
    # Method: Queue real controller progress while preserving an unsaved target name.
    # ==========================================
    def test_progress_updates_fixed_cards_and_preserves_editor(self):
        app,g=self.app,self.g
        g.text(app.name_entry,'尚未保存的修改')
        shot={'venue':'学生中心','sport':'健身房','active_date':app.model.day(),'columns':['场地1'],
              'times':['14:00-15:00'],'rows':[['available']]}
        with patch.object(self.controller,'state',side_effect=AssertionError('synchronous state read')):
            self.controller.log('正在检查测试场地')
            self.controller.progress({'message':'本地进度','goals':[{'id':app.model.selected,'status':'pending','watch_state':'checking'}],'snapshot':shot})
            app.tick()
        self.assertEqual(g.text(app.name_entry),'尚未保存的修改')
        self.assertIn('正在检查测试场地','\n'.join(app.logs))
        self.assertEqual(app.snapshot,shot)
        self.assertTrue(g.children(app.w['inventory']))
        self.assertEqual(app.errors,[])

    # ==========================================
    # Function: Keep GTK responsive when configuration persistence is slow.
    # Method: Delay only the save worker and require an independent GLib callback before it completes.
    # ==========================================
    def test_slow_save_keeps_native_loop_responsive(self):
        app,g=self.app,self.g
        original=self.controller.save
        beats=[]

        # ==========================================
        # Function: Delay isolated disk persistence without blocking GTK.
        # Method: Sleep only on the existing asynchronous save thread.
        # ==========================================
        def slow_save(data):
            time.sleep(.2)
            original(data)

        with patch.object(self.controller,'save',side_effect=slow_save):
            app.save()
            g.later(15,lambda:beats.append(True))
            self.pump(.08)
            self.assertEqual(beats,[True])
            self.assertTrue(app.saving)
            app.save_worker.join(2)
            self.pump(.12)
        self.assertFalse(app.saving)
        self.assertEqual(app.errors,[])

    # ==========================================
    # Function: Keep success notifications nonblocking and close-only.
    # Method: Publish duplicate confirmed events during monitoring and run a separate GLib heartbeat.
    # ==========================================
    def test_completion_is_close_only_and_deduplicated(self):
        app,g=self.app,self.g
        item={'id':app.model.selected,'name':'运动目标','detail':{'order_id':'LOCAL-1','venue':'学生中心','sport':'健身房','court':'场地1','date':app.model.day(),'slots':['14:00-15:00']}}
        app.show_completion(item)
        app.show_completion(item)
        self.assertEqual(len(app.notices),1)
        titlebar=g.gtk_window_get_titlebar(app.notice_window)
        self.assertEqual(g.gtk_header_bar_get_decoration_layout(titlebar),b':close')
        beats=[]
        g.later(10,lambda:beats.append(True))
        self.pump()
        self.assertEqual(beats,[True])
        self.assertFalse(self.controller.stop_event.is_set())

    # ==========================================
    # Function: Preserve invalid settings without saving partial configuration.
    # Method: Enter an out-of-range interval and require validation before model replacement.
    # ==========================================
    def test_invalid_settings_are_atomic(self):
        app,g=self.app,self.g
        before=deepcopy(app.model.data)
        app.open_settings()
        g.text(app.settings_fields['poll_interval_seconds'],'1')
        app.guard(app.apply_settings)
        self.assertEqual(app.model.data,before)
        self.assertTrue(app.settings_window)
        self.assertTrue(app.errors)

    # ==========================================
    # Function: Deliver native wheel events over scroller padding rather than a time button.
    # Method: Construct public GdkEventScroll values and let GtkScrolledWindow handle them directly.
    # ==========================================
    def wheel(self, widget, direction):
        # ==========================================
        # Function: Match GTK 3's public scroll-event layout for synthetic input tests.
        # Method: Preserve native enum, pointer alignment, deltas, and stop-bit storage.
        # ==========================================
        class Scroll(C.Structure):
            _fields_=[('type',C.c_int),('window',C.c_void_p),('send_event',C.c_int8),('time',C.c_uint32),
                      ('x',C.c_double),('y',C.c_double),('state',C.c_uint),('direction',C.c_int),
                      ('device',C.c_void_p),('x_root',C.c_double),('y_root',C.c_double),
                      ('delta_x',C.c_double),('delta_y',C.c_double),('is_stop',C.c_uint)]
        g=self.g
        lib=g.libs['gdk']
        signatures={'gdk_event_new':(C.c_void_p,C.c_int),'gdk_event_free':(None,C.c_void_p),
                    'gdk_display_get_default':(C.c_void_p,),'gdk_display_get_default_seat':(C.c_void_p,C.c_void_p),
                    'gdk_seat_get_pointer':(C.c_void_p,C.c_void_p),'gdk_event_set_device':(None,C.c_void_p,C.c_void_p)}
        for name,signature in signatures.items():
            function=getattr(lib,name);function.restype,function.argtypes=signature[0],signature[1:]
        event=lib.gdk_event_new(31)
        scroll=C.cast(event,C.POINTER(Scroll)).contents
        scroll.window=g.g_object_ref(g.gtk_widget_get_window(widget))
        scroll.send_event,scroll.x,scroll.y,scroll.direction=1,2,2,direction
        device=lib.gdk_seat_get_pointer(lib.gdk_display_get_default_seat(lib.gdk_display_get_default()))
        lib.gdk_event_set_device(event,device)
        try:
            return g.gtk_widget_event(widget,event)
        finally:
            lib.gdk_event_free(event)

    # ==========================================
    # Function: Scroll slots over their empty padding and stop sidebar upward overscroll at zero.
    # Method: Send real native scroll signals without hovering or activating any time tile.
    # ==========================================
    def test_wheel_over_padding_and_top_boundary(self):
        app,g=self.app,self.g
        adjustment=g.gtk_scrolled_window_get_vadjustment(app.hour_scroll)
        g.gtk_adjustment_set_value(adjustment,0)
        self.assertTrue(self.wheel(app.hour_scroll,1))
        self.pump(.22)
        self.assertGreater(g.gtk_adjustment_get_value(adjustment),0)
        sidebar=g.gtk_scrolled_window_get_vadjustment(app.w['goal_scroll'])
        g.gtk_adjustment_set_value(sidebar,0)
        self.wheel(app.w['goal_scroll'],0)
        self.pump(.12)
        self.assertEqual(g.gtk_adjustment_get_value(sidebar),0)

    # ==========================================
    # Function: Save settings through the child window without blocking the native main loop.
    # Method: Commit a valid interval, wait for the worker, and require the window to close only on success.
    # ==========================================
    def test_settings_save_roundtrip_and_close(self):
        app,g=self.app,self.g
        app.open_settings()
        g.text(app.settings_fields['poll_interval_seconds'],'27')
        g.gtk_button_clicked(app.settings_save)
        app.save_worker.join(2)
        self.pump(.12)
        self.assertEqual(load_config(self.controller.config_path).poll_interval_seconds,27)
        self.assertIsNone(app.settings_window)
        self.assertEqual(app.errors,[])

    # ==========================================
    # Function: Avoid false validation errors when save disables a focused repeat-time entry.
    # Method: Focus the time field, save, and exercise its native focus-out path while saving.
    # ==========================================
    def test_repeat_save_does_not_raise_focus_out_busy_error(self):
        app,g=self.app,self.g
        app.toggle_repeat()
        g.gtk_widget_grab_focus(app.repeat_at)
        app.save()
        app.schedule_focus_out()
        app.save_worker.join(2)
        self.pump(.1)
        self.assertEqual(app.errors,[])

    # ==========================================
    # Function: Wait for the active worker before destroying the native workspace.
    # Method: Simulate a running operation and complete it only after the close request is observed.
    # ==========================================
    def test_close_waits_for_monitor_completion(self):
        self.controller.busy,self.controller.operation=True,'run'
        self.app.request_close()
        self.pump()
        self.assertFalse(self.app.destroyed)
        self.assertTrue(self.controller.stop_event.is_set())
        self.controller.busy,self.controller.operation=False,'idle'
        self.app.tick()
        self.assertTrue(self.app.destroyed)

    # ==========================================
    # Function: Show status on row surfaces and accents rather than font color alone.
    # Method: Compare actual GTK background colors for planned, running and recurring rows.
    # ==========================================
    def test_goal_status_surfaces_have_distinct_colors(self):
        # ==========================================
        # Function: Match GTK's public RGBA color structure for rendered-style inspection.
        # Method: Read resolved native CSS colors, including selected-row overrides.
        # ==========================================
        class RGBA(C.Structure):
            _fields_=[('red',C.c_double),('green',C.c_double),('blue',C.c_double),('alpha',C.c_double)]
        app,g=self.app,self.g
        read=g.libs['gtk'].gtk_style_context_get_background_color
        read.argtypes,read.restype=[C.c_void_p,C.c_uint,C.POINTER(RGBA)],None

        # ==========================================
        # Function: Capture a selected row's resolved surface color.
        # Method: Wait for native style transitions and query GTK_STATE_FLAG_SELECTED.
        # ==========================================
        def color():
            self.pump(.22)
            row=app.sidebar_rows[app.model.selected][0]
            value=RGBA()
            read(g.gtk_widget_get_style_context(row),4,C.byref(value))
            self.assertGreater(value.alpha,.9)
            return (value.red,value.green,value.blue)

        row=app.sidebar_rows[app.model.selected][0]
        self.assertTrue(g.gtk_style_context_has_class(g.gtk_widget_get_style_context(row),b'planned'))
        planned=color()
        self.controller.busy,self.controller.operation=True,'run'
        app.set_busy()
        self.assertTrue(g.gtk_style_context_has_class(g.gtk_widget_get_style_context(row),b'running'))
        running=color()
        self.controller.busy,self.controller.operation=False,'idle'
        app.toggle_repeat()
        recurring=color()
        self.assertGreater(sum(abs(a-b) for a,b in zip(planned,running)),.08)
        self.assertGreater(sum(abs(a-b) for a,b in zip(running,recurring)),.08)
        app.model.dirty=False

    # ==========================================
    # Function: Avoid reassigning unchanged captions on every status refresh.
    # Method: Spy on native label updates while refreshing a stable sidebar twice.
    # ==========================================
    def test_unchanged_sidebar_skips_caption_updates(self):
        app=self.app
        labels=set(app.sidebar_rows[app.model.selected][1:3])
        original=self.g.label
        touched=[]

        # ==========================================
        # Function: Observe only sidebar text mutations during an unchanged redraw.
        # Method: Delegate to the actual GTK setter after recording relevant widget identities.
        # ==========================================
        def track(widget,value):
            if widget in labels:touched.append(widget)
            original(widget,value)

        with patch.object(self.g,'label',side_effect=track):
            app.render_sidebar()
            app.render_sidebar()
        self.assertEqual(touched,[])

    # ==========================================
    # Function: Keep three ordered workbench sections independently collapsible without editing booking data.
    # Method: Change native expanders, refresh the editor and check retained per-goal folds and fixed monitors.
    # ==========================================
    def test_three_sections_remember_independent_folds(self):
        app,g=self.app,self.g
        sections=[app.repeat_expander,app.rush_expander,app.target_expander]
        self.assertTrue(all(g.gtk_widget_get_parent(section)==app.w['editor'] for section in sections))
        app.model.dirty=False
        for section in sections:
            g.gtk_expander_set_expanded(section,False)
        self.pump()
        self.assertFalse(app.model.dirty)
        app.load_editor()
        self.assertTrue(all(not g.gtk_expander_get_expanded(section) for section in sections))
        g.gtk_expander_set_expanded(app.rush_expander,True)
        app.render_rush()
        self.assertFalse(g.gtk_expander_get_expanded(app.repeat_expander))
        self.assertFalse(g.gtk_expander_get_expanded(app.target_expander))
        self.assertTrue(g.gtk_widget_get_mapped(app.rush_switch))
        self.assertTrue(g.gtk_widget_get_mapped(app.w['logs']))
        self.assertEqual(app.errors,[])

    # ==========================================
    # Function: Persist rush controls per goal while preserving next-day copies and weekly clock semantics.
    # Method: Operate actual GTK switches, spin buttons and repeat conversion, then reload the saved config.
    # ==========================================
    def test_rush_switch_copy_and_weekly_persistence(self):
        app,g=self.app,self.g
        source=app.model.selected
        release=datetime.now(ZoneInfo('Asia/Shanghai')).replace(microsecond=0)+timedelta(hours=2)
        g.text(app.rush_at,release.isoformat())
        g.gtk_switch_set_active(app.rush_switch,True)
        g.gtk_spin_button_set_value(app.rush_spins['prewarm_seconds'],60)
        self.assertTrue(app.model.get()['rush']['enabled'])
        self.assertTrue(g.gtk_expander_get_expanded(app.rush_expander))
        app.copy_goal(source)
        self.assertEqual(datetime.fromisoformat(app.model.get()['rush']['at']),release+timedelta(days=1))
        g.gtk_switch_set_active(app.rush_switch,False)
        self.assertTrue(next(goal for goal in app.model.data['goals'] if goal['id']==source)['rush']['enabled'])
        g.gtk_switch_set_active(app.rush_switch,True)
        app.toggle_repeat()
        self.assertEqual(app.model.get()['rush']['at'],release.strftime('%H:%M:%S'))
        self.assertTrue(g.gtk_switch_get_active(app.rush_switch))
        app.save()
        app.save_worker.join(2)
        self.pump(.12)
        saved=load_config(self.controller.config_path)
        self.assertTrue(saved.goals[0].rush.enabled)
        self.assertTrue(saved.repeat_rules[0].rush.enabled)
        self.assertEqual(saved.repeat_rules[0].rush.prewarm_seconds,60)
        self.assertEqual(saved.repeat_rules[0].rush.at,release.strftime('%H:%M:%S'))
        self.assertEqual(app.errors,[])

    # ==========================================
    # Function: Show live release timing with explicit uncertainty without reopening collapsed sections.
    # Method: Feed production-shaped progress and read the actual native countdown label.
    # ==========================================
    def test_rush_progress_keeps_fold_and_clock_uncertainty(self):
        app,g=self.app,self.g
        g.gtk_switch_set_active(app.rush_switch,True)
        g.gtk_expander_set_expanded(app.rush_expander,False)
        app.model.states[app.model.selected]={'status':'pending','rush':{
            'stage':'提前准备','deadline_monotonic':time.monotonic()+30,
            'clock':'HTTP Date 估算偏差 +120ms · 误差约 ±550ms'}}
        self.controller.busy,self.controller.operation=True,'run'
        app.render_rush()
        caption=g.gtk_label_get_text(app.rush_status).decode()
        self.assertIn('距开抢',caption)
        self.assertIn('±550ms',caption)
        self.assertFalse(g.gtk_expander_get_expanded(app.rush_expander))
        self.controller.busy,self.controller.operation=False,'idle'
        app.model.dirty=False
