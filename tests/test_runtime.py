"""Regression coverage for shell/interpreter and binary dependency mismatches."""

from contextlib import redirect_stdout
import builtins
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from autoreserve.browser import require_playwright
from autoreserve.gui import serve_gui
from scripts.launch import runtime_environment, select_runtime


# ==========================================
# Function: Validate runtime selection without installing or changing environments.
# Method: Mock interpreter import probes and exercise user-facing failure diagnostics.
# ==========================================
class RuntimeTests(unittest.TestCase):
    # ==========================================
    # Function: Recover when the shell selects an ABI-incompatible Python.
    # Method: Simulate a broken native extension, then select a validated interpreter.
    # ==========================================
    def test_skip_incompatible_interpreter(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "python312", Path(directory) / "python314"
            for path in (first, second):
                path.write_text("test interpreter")
                path.chmod(0o700)
            failed = subprocess.CompletedProcess([], 1, "", "ModuleNotFoundError: No module named 'greenlet._greenlet'")
            success = subprocess.CompletedProcess([], 0, json.dumps({"version": "3.14.4", "python": str(second)}), "")
            with patch("scripts.launch.interpreter_candidates", return_value=iter([(first, True), (second, True)])), \
                 patch("scripts.launch.subprocess.run", side_effect=[failed, success]) as probe:
                executable, env, info = select_runtime(Path(directory))
            self.assertEqual(executable, str(second))
            self.assertEqual(info["version"], "3.14.4")
            self.assertEqual(probe.call_count, 2)
            self.assertIn("autoreserve-deps", env["PYTHONPATH"])

    # ==========================================
    # Function: Keep a complete project environment independent of managed packages.
    # Method: Prefer a successful venv import and remove inherited managed paths.
    # ==========================================
    def test_complete_project_environment_is_preferred(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "python"
            executable.write_text("test interpreter")
            executable.chmod(0o700)
            managed = Path.home() / ".local/pixi-apps/autoreserve-deps"
            result = subprocess.CompletedProcess([], 0, json.dumps({"version": "3.12.13", "python": str(executable)}), "")
            with patch.dict(os.environ, {"PYTHONPATH": str(managed), "PYTHONHOME": "/unrelated/python"}), \
                 patch("scripts.launch.interpreter_candidates", return_value=iter([(executable, False)])), \
                 patch("scripts.launch.subprocess.run", return_value=result):
                selected, env, _ = select_runtime(Path(directory))
                self.assertEqual(os.environ["PYTHONHOME"], "/unrelated/python")
            self.assertEqual(selected, str(executable))
            self.assertNotIn("PYTHONPATH", env)
            self.assertNotIn("PYTHONHOME", env)

    # ==========================================
    # Function: Diagnose an unavailable binary extension accurately.
    # Method: Raise the observed greenlet import error through the production helper.
    # ==========================================
    def test_binary_import_error_is_not_reported_as_missing_playwright(self):
        original = builtins.__import__
        # ==========================================
        # Function: Reproduce only the failing browser import.
        # Method: Delegate unrelated imports to Python's standard import mechanism.
        # ==========================================
        def import_failure(name, *args, **kwargs):
            if name == "playwright.sync_api":
                raise ModuleNotFoundError("No module named 'greenlet._greenlet'", name="greenlet._greenlet")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=import_failure):
            with self.assertRaises(RuntimeError) as caught:
                require_playwright()
        message = str(caught.exception)
        self.assertIn("greenlet._greenlet", message)
        self.assertIn("Python 版本不匹配", message)
        self.assertNotIn("未找到 Playwright", message)
        self.assertIn(sys.executable, message)

    # ==========================================
    # Function: Report a truly absent Playwright package separately.
    # Method: Inject a top-level missing-package exception.
    # ==========================================
    def test_missing_package_diagnostic(self):
        original = builtins.__import__
        # ==========================================
        # Function: Simulate an interpreter with no Playwright installation.
        # Method: Restrict the injected failure to the browser package import.
        # ==========================================
        def import_failure(name, *args, **kwargs):
            if name == "playwright.sync_api":
                raise ModuleNotFoundError("No module named 'playwright'", name="playwright")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=import_failure):
            with self.assertRaisesRegex(RuntimeError, "当前解释器未找到 Playwright"):
                require_playwright()

    # ==========================================
    # Function: Avoid announcing a GUI URL before a dependency failure.
    # Method: Require preflight to fail before the server binds or prints readiness.
    # ==========================================
    def test_gui_preflight_happens_before_server_creation(self):
        output = io.StringIO()
        with patch("autoreserve.gui.require_playwright", side_effect=RuntimeError("incompatible runtime")), \
             patch("autoreserve.gui.make_server") as make_server, redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, "incompatible"):
                serve_gui("unused.json", "unused-auth")
        make_server.assert_not_called()
        self.assertEqual(output.getvalue(), "")

    # ==========================================
    # Function: Load approved native Tk resources without modifying the parent shell.
    # Method: Construct an isolated native tree and inspect the child runtime environment.
    # ==========================================
    def test_isolated_native_tk_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            managed = Path(directory)
            (managed / ".native/usr").mkdir(parents=True)
            (managed / ".fonts").mkdir()
            (managed / ".fonts/fonts.conf").write_text("font fixture")
            with patch.dict(os.environ, {"PYTHONPATH": str(managed / "stale") + os.pathsep + "/other/modules", "LD_LIBRARY_PATH": "/other/libs"}):
                result = runtime_environment(managed, True)
                self.assertIn(str(managed / "stale"), os.environ["PYTHONPATH"])
            self.assertNotIn(str(managed / "stale"), result["PYTHONPATH"])
            self.assertIn("lib-dynload", result["PYTHONPATH"])
            self.assertIn("/other/modules", result["PYTHONPATH"])
            self.assertTrue(result["TCL_LIBRARY"].endswith("tcl8.6"))
            self.assertTrue(result["TK_LIBRARY"].endswith("tk8.6"))
            self.assertTrue(result["LD_LIBRARY_PATH"].endswith("/other/libs"))

    # ==========================================
    # Function: Route every normal GUI entry to the native client rather than an HTTP server.
    # Method: Exercise default, gui alias, and desktop CLI dispatch without creating windows.
    # ==========================================
    def test_gui_entrypoints_are_native(self):
        from autoreserve.__main__ import main
        with patch("autoreserve.desktop.serve_desktop", return_value=0) as desktop, \
             patch("autoreserve.gui.serve_gui", side_effect=AssertionError("web GUI")):
            for argv in ([], ["gui"], ["desktop"]):
                self.assertEqual(main(argv), 0)
        self.assertEqual(desktop.call_count, 3)


    # ==========================================
    # Function: Keep normal production entry points usable without the historical Tk toolkit.
    # Method: Block Tk imports in a fresh interpreter and route all GUI commands to a mocked GTK shell.
    # ==========================================
    def test_production_entries_do_not_import_tk(self):
        script = """import builtins
original = builtins.__import__
def without_tk(name, *args, **kwargs):
    if name == 'tkinter' or name.startswith('tkinter.') or name == '_tkinter':
        raise ImportError('Tk intentionally unavailable')
    return original(name, *args, **kwargs)
builtins.__import__ = without_tk
from unittest.mock import patch
from autoreserve.__main__ import main
with patch('autoreserve.gtk_desktop.serve_gtk', return_value=0) as shell:
    for args in ([], ['gui'], ['desktop']):
        assert main(args) == 0
    assert shell.call_count == 3
"""
        result = subprocess.run([sys.executable, '-c', script],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
