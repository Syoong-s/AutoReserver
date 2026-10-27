"""Loopback-only native Windows verification invoked by the packaged EXE self-test."""

import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest


# ==========================================
# Function: Exercise Windows GTK controls and a real local Chromium booking without an account or live website.
# Method: Run selected production regression cases using temporary files and save an isolated native preview/report.
# ==========================================
def self_test(root):
    root = Path(root)
    os.environ['RUN_GTK_TESTS'] = '1'
    os.environ['RUN_BROWSER_TESTS'] = '1'
    sys.path.insert(0, str(root / 'tests'))
    from test_gtk import GtkWorkspaceTests
    from test_browser import BrowserTests
    from test_rush_browser import RushBrowserTests
    from autoreserve import __version__
    from autoreserve.gtk_native import GtkNative
    from autoreserve.state import account_lock
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
    cases = [GtkWorkspaceTests(name) for name in (
        'test_settings_first_frame_has_native_close_only_header',
        'test_three_sections_remember_independent_folds',
        'test_rush_switch_copy_and_weekly_persistence',
        'test_sidebar_native_adjustment_clamps_at_top',
        'test_rush_progress_keeps_fold_and_clock_uncertainty')]
    cases.extend([BrowserTests('test_submit_stops_before_payment'),
                  RushBrowserTests('test_release_gates_selection_and_discards_prewarm_stock')])
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(cases))
    if not result.wasSuccessful():
        raise RuntimeError('Windows 本地验证失败，请查看运行日志')
    preview = GtkWorkspaceTests('test_three_sections_remember_independent_folds')
    preview.setUp()
    try:
        app, g = preview.app, preview.g
        g.gtk_switch_set_active(app.rush_switch, True)
        g.gtk_window_resize(app.window, 1260, 970)
        g.gtk_expander_set_expanded(app.target_expander, False)
        preview.pump(.3)
        g.capture(app.window, root / '.runtime/logs/windows-workbench.png')
        app.model.dirty = False
        gtk_version = '.'.join(str(f()) for f in (g.gtk_get_major_version, g.gtk_get_minor_version, g.gtk_get_micro_version))
    finally:
        preview.tearDown()
    from scripts.bootstrap_windows import atomic_json
    atomic_json(root / '.runtime/logs/windows-smoke.json', {
        'status': 'passed', 'version': __version__, 'platform': sys.platform,
        'python': sys.version, 'gtk': gtk_version, 'tests_run': result.testsRun,
        'account_lock_verified': True, 'real_reservations': 0, 'payments': 0})
