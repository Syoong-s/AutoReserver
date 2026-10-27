"""Self-contained Windows window/browser diagnostics, with optional source-tree CI regressions."""

import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest


# ==========================================
# Function: Pump the native event loop for bounded window-manager transitions.
# Method: Drain GTK events while allowing Win32 activation/minimize messages to settle.
# ==========================================
def pump(g, seconds=.15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while g.gtk_events_pending():
            g.gtk_main_iteration_do(False)
        time.sleep(.002)


# ==========================================
# Function: Verify actual Win32 window styles, icon, taskbar commands and monitor placement.
# Method: Send system minimize/restore commands to this test's own GTK HWND and inspect native state.
# ==========================================
def check_window(app):
    g = app.g
    user = C.WinDLL('user32', use_last_error=True)
    handle = g.libs['gdk'].gdk_win32_window_get_handle
    handle.argtypes, handle.restype = [C.c_void_p], W.HWND
    hwnd = handle(g.gtk_widget_get_window(app.window))
    user.GetWindowLongPtrW.argtypes, user.GetWindowLongPtrW.restype = [W.HWND, C.c_int], C.c_ssize_t
    user.SendMessageW.argtypes, user.SendMessageW.restype = [W.HWND, W.UINT, C.c_size_t, C.c_ssize_t], C.c_ssize_t
    user.IsIconic.argtypes, user.IsIconic.restype = [W.HWND], W.BOOL
    user.GetWindowRect.argtypes, user.GetWindowRect.restype = [W.HWND, C.POINTER(W.RECT)], W.BOOL
    user.MonitorFromWindow.argtypes, user.MonitorFromWindow.restype = [W.HWND, W.DWORD], W.HMONITOR
    user.GetMonitorInfoW.argtypes, user.GetMonitorInfoW.restype = [W.HMONITOR, C.c_void_p], W.BOOL
    style = user.GetWindowLongPtrW(hwnd, -16)
    assert style & 0x00CF0000 == 0x00CF0000, 'Missing native caption/resize/minimize/maximize controls'
    assert not g.gtk_window_get_titlebar(app.window), 'Main workspace must use the Windows titlebar'
    assert g.gtk_window_get_icon(app.window), 'Missing AutoReserve GTK icon'
    assert user.SendMessageW(hwnd, 0x007F, 1, 0), 'Missing native large taskbar icon'
    identity = C.c_wchar_p()
    shell = C.WinDLL('shell32')
    shell.GetCurrentProcessExplicitAppUserModelID.argtypes = [C.POINTER(C.c_wchar_p)]
    shell.GetCurrentProcessExplicitAppUserModelID.restype = C.c_long
    ole = C.WinDLL('ole32')
    ole.CoTaskMemFree.argtypes = [C.c_void_p]
    assert shell.GetCurrentProcessExplicitAppUserModelID(C.byref(identity)) == 0
    try:
        assert identity.value == 'org.autoreserve.Desktop', 'Window still belongs to Python taskbar group'
    finally:
        ole.CoTaskMemFree(C.cast(identity, C.c_void_p))
    for _ in range(3):
        user.SendMessageW(hwnd, 0x0112, 0xF020, 0)  # WM_SYSCOMMAND / SC_MINIMIZE
        pump(g)
        assert user.IsIconic(hwnd), 'System taskbar minimize did not minimize the workspace'
        g.gtk_window_present(app.window)
        pump(g)
        assert not user.IsIconic(hwnd), 'Present did not restore the minimized workspace'
        assert g.gtk_widget_get_mapped(app.window)

    # ==========================================
    # Function: Read the physical work area belonging to the realized HWND.
    # Method: Match MONITORINFO so taskbar exclusions and multi-monitor origins are preserved.
    # ==========================================
    class MonitorInfo(C.Structure):
        _fields_ = [('size', W.DWORD), ('monitor', W.RECT), ('work', W.RECT), ('flags', W.DWORD)]

    info, rect = MonitorInfo(), W.RECT()
    info.size = C.sizeof(info)
    assert user.GetMonitorInfoW(user.MonitorFromWindow(hwnd, 2), C.byref(info))
    assert user.GetWindowRect(hwnd, C.byref(rect))
    work = info.work
    assert (rect.left >= work.left-8 and rect.top >= work.top and
            rect.right <= work.right+8 and rect.bottom <= work.bottom+8), (
                'Window outside monitor work area', tuple(getattr(rect, n) for n in ('left','top','right','bottom')),
                tuple(getattr(work, n) for n in ('left','top','right','bottom')))
    assert not g.signal_errors, g.signal_errors
    return {'minimize_restore_cycles': 3, 'native_titlebar': True, 'app_icon': True,
            'app_user_model_id': 'org.autoreserve.Desktop', 'inside_workarea': True}


# ==========================================
# Function: Exercise the portable runtime independently of tests, demo files or repository metadata.
# Method: Use temporary empty goals, local HTML and owned GTK windows; optionally run deeper CI cases from source.
# ==========================================
def self_test(root, test_source=None):
    root = Path(root)
    from autoreserve import __version__
    from autoreserve.config import Config, save_config
    from autoreserve.desktop_controller import DesktopController
    from autoreserve.gtk_desktop import GtkDesktop
    from autoreserve.state import account_lock
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as directory:
        code = ('import sys; from autoreserve.state import account_lock\n'
                'try:\n with account_lock(sys.argv[1]): print("acquired")\n'
                'except RuntimeError:\n print("blocked")')
        args = [sys.executable, '-I', '-X', 'utf8', '-c', code, directory]
        with account_lock(directory):
            locked = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', timeout=30, creationflags=0x08000000)
            if locked.returncode or locked.stdout.strip() != 'blocked':
                raise RuntimeError('Windows account lock did not exclude a second process')
        unlocked = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', timeout=30, creationflags=0x08000000)
        if unlocked.returncode or unlocked.stdout.strip() != 'acquired':
            raise RuntimeError('Windows account lock was not released')
        config = Path(directory) / 'config.json'
        save_config(Config(), config)
        app = GtkDesktop(DesktopController(config, Path(directory) / 'auth'), auto_catalog=False)
        g = app.g
        try:
            g.gtk_window_present(app.window)
            pump(g, .3)
            window_report = check_window(app)
            g.capture(app.window, root / '.runtime/logs/windows-workbench.png')
            gtk_version = '.'.join(str(f()) for f in (g.gtk_get_major_version, g.gtk_get_minor_version, g.gtk_get_micro_version))
        finally:
            app.model.dirty = False
            app.destroy()
            pump(g)
            for source in list(g.sources):
                g.cancel(source)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content('<button>AutoReserve local test</button>')
                page.get_by_role('button').click()
            finally:
                browser.close()
    tests_run = 0
    if test_source:
        os.environ['RUN_GTK_TESTS'] = os.environ['RUN_BROWSER_TESTS'] = '1'
        sys.path.insert(0, str(Path(test_source) / 'tests'))
        sys.path.append(str(Path(test_source)))
        from test_gtk import GtkWorkspaceTests
        from test_browser import BrowserTests
        from test_rush_browser import RushBrowserTests
        from test_catalog_browser import DirectoryBrowserTests
        from test_multivenue import MultiVenueTests
        from test_goal_actions import GoalActionTests
        cases_backend=unittest.defaultTestLoader.loadTestsFromTestCase(GoalActionTests)
        cases = [GtkWorkspaceTests(name) for name in (
            'test_settings_first_frame_has_native_close_only_header',
            'test_three_sections_remember_independent_folds',
            'test_rush_switch_copy_and_weekly_persistence',
            'test_sidebar_native_adjustment_clamps_at_top',
            'test_rush_progress_keeps_fold_and_clock_uncertainty',
            'test_progress_batch_preserves_latest_inventory',
            'test_logs_keep_fifty_lines_and_always_follow_tail',
            'test_candidate_composer_preserves_drafts_on_dismiss',
            'test_font_size_persists_without_changing_booking')]
        cases.extend(GtkWorkspaceTests(name) for name in (
            'test_selected_toolbar_save_retains_unrelated_incomplete_draft',
            'test_toolbar_start_uses_captured_goal_identity',
            'test_completed_sidebar_delete_and_history_record_delete',
            'test_closed_history_window_ignores_inflight_read'))
        cases.extend(GtkWorkspaceTests(name) for name in (
            'test_native_dividers_resize_all_workspace_panels',
            'test_layout_preferences_restore_and_keep_font'))
        cases.extend([BrowserTests('test_submit_stops_before_payment'),
                      BrowserTests('test_history_delete_allows_new_goal_for_identical_slot'),
                      BrowserTests('test_zero_duration_and_price_allow_unlimited_preview'),
                      MultiVenueTests('test_zero_duration_continues_until_stopped'),
                      RushBrowserTests('test_release_gates_selection_and_discards_prewarm_stock')])
        cases.extend(DirectoryBrowserTests(name) for name in (
            'test_first_login_recovers_slow_directory_response',
            'test_directory_timeout_preserves_captured_login',
            'test_browser_abort_error_retries_once'))
        result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([*cases,*cases_backend]))
        if not result.wasSuccessful():
            raise RuntimeError('Windows 本地验证失败，请查看运行日志')
        tests_run = result.testsRun
    from scripts.bootstrap_windows import atomic_json
    atomic_json(root / '.runtime/logs/windows-smoke.json', {
        'status': 'passed', 'version': __version__, 'platform': sys.platform,
        'python': sys.version, 'gtk': gtk_version, 'tests_run': tests_run,
        'window': window_report, 'account_lock_verified': True, 'real_reservations': 0, 'payments': 0})
