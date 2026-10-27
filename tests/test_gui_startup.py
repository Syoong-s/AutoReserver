"""Read-only rendering diagnostics and real launcher visibility regression tests."""

import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import time
import unittest

from autoreserve.config import save_config
from autoreserve.gui_startup import present_window, wslg_render_warning
from autoreserve.state import private_json
from test_catalog import directory_fixture, native_config


# ==========================================
# Function: Diagnose current WSLg rendering without exposing logs or changing services.
# Method: Supply isolated logs representing failed and recovered sessions.
# ==========================================
class DisplayDiagnosticTests(unittest.TestCase):
    # ==========================================
    # Function: Warn when the latest WSLg session uses the broken copy fallback.
    # Method: Read only a private fixture, leaving its contents untouched.
    # ==========================================
    def test_copy_mode_warning_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "weston.log"
            content = 'rdp_allocate_shared_memory: Failed to open "/mnt/shared_memory/{test}" with error: Input/output error\nRDP backend: use_gfxredir = 0\n'
            log.write_text(content)
            self.assertIn("COPY MODE", wslg_render_warning(log))
            self.assertEqual(log.read_text(), content)

    # ==========================================
    # Function: Avoid reporting historical failures after the rendering service recovers.
    # Method: Prefer the last backend state and ignore the unrelated warning-title option.
    # ==========================================
    def test_newest_healthy_backend_overrides_old_error(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "weston.log"
            log.write_text("RDP backend: use_gfxredir = 0\nRDP backend: use_gfxredir = 1\nRDP backend: enable_copy_warning_title = 1\n")
            self.assertIsNone(wslg_render_warning(log))
            with log.open("a") as handle:
                handle.write("RDP backend: use_gfxredir = 0\n")
            self.assertIn("COPY MODE", wslg_render_warning(log))

    # ==========================================
    # Function: Keep ordinary Linux launches working without WSLg logs.
    # Method: Treat missing or unknown diagnostics as no detected rendering failure.
    # ==========================================
    def test_missing_or_unrecognized_log_is_not_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "weston.log"
            self.assertIsNone(wslg_render_warning(log))
            log.write_text("ordinary compositor output\n")
            self.assertIsNone(wslg_render_warning(log))

    # ==========================================
    # Function: Bound startup reads even when the compositor log has grown large.
    # Method: Put the newest valid state beyond two megabytes of old records.
    # ==========================================
    def test_large_log_uses_latest_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "weston.log"
            log.write_text("RDP backend: use_gfxredir = 0\n" + "x" * (2 * 1024 * 1024) + "\nRDP backend: use_gfxredir = 1\n")
            self.assertIsNone(wslg_render_warning(log))


# ==========================================
# Function: Verify real native window mapping and the user's shell launcher.
# Method: Run under WSLg with isolated config/catalog paths and never start monitoring.
# ==========================================
@unittest.skipUnless(os.environ.get("RUN_NATIVE_TESTS") == "1", "设置 RUN_NATIVE_TESTS=1 启用原生启动测试")
class NativeStartupTests(unittest.TestCase):
    # ==========================================
    # Function: Unhide a prepared window and remove temporary topmost status afterward.
    # Method: Observe actual root Map events and run the normal native event loop.
    # ==========================================
    def test_window_is_mapped_once_and_topmost_is_released(self):
        import tkinter as tk
        root = tk.Tk(className="AutoReserveTest")
        root.withdraw()
        root.title("AutoReserve startup test")
        root.geometry("360x120")
        calls = []
        tk.Label(root, text="Startup regression test").pack()
        try:
            present_window(root, lambda: calls.append(root.winfo_ismapped()))
            root.after(550, root.quit)
            root.mainloop()
            self.assertEqual(calls, [1])
            self.assertEqual(root.state(), "normal")
            self.assertTrue(root.winfo_viewable())
            self.assertFalse(root.attributes("-topmost"))
        finally:
            for job in root.tk.call("after", "info"):
                root.after_cancel(job)
            root.destroy()

    # ==========================================
    # Function: Exercise the exact start-gui script, including explicit config/auth arguments.
    # Method: Wait for real window mapping output, then stop only the isolated child client.
    # ==========================================
    def test_shell_launcher_maps_real_client_window(self):
        project = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            config = native_config()
            config.workspace_version = 2
            save_config(config, path / "config.json")
            private_json(path / "auth/catalog.json", directory_fixture())
            process = subprocess.Popen([str(project / "start-gui.sh"), "--config", str(path / "config.json"),
                                        "--auth-dir", str(path / "auth")], cwd=project,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            output = b""
            try:
                deadline = time.monotonic() + 15
                marker = "客户端窗口已创建并映射".encode()
                while time.monotonic() < deadline and marker not in output:
                    readable, _, _ = select.select([process.stdout], [], [], 0.1)
                    if readable:
                        chunk = os.read(process.stdout.fileno(), 4096)
                        if not chunk:
                            break
                        output += chunk
                    if process.poll() is not None:
                        break
                self.assertIn(marker, output, output.decode(errors="replace"))
                self.assertIsNone(process.poll())
                self.assertFalse((path / "auth/session.json").exists())
                self.assertFalse((path / "artifacts").exists())
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
