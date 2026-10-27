"""Native launcher relocation and freedesktop quoting checks using temporary bundles."""

import ctypes as C
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from autoreserve.desktop_install import desktop_entry, install_menu
from tools.release import build_launcher


# ==========================================
# Function: Verify mouse-launch integration without modifying the real user's application menu.
# Method: Compile a temporary ELF and launch test entries through the actual GIO desktop parser.
# ==========================================
class DesktopLaunchTests(unittest.TestCase):
    # ==========================================
    # Function: Preserve portable-launch arguments through relocation and reserved path characters.
    # Method: Run a real native entry against a recording bootstrap in a path with spaces and percent signs.
    # ==========================================
    @unittest.skipUnless(shutil.which('cc') or shutil.which('gcc'), 'C compiler needed for native launcher test')
    def test_elf_relocation_and_argument_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / '移动 folder 50% $money `name`'
            (root / 'native').mkdir(parents=True)
            (root / 'scripts').mkdir()
            source = Path(__file__).resolve().parents[1] / 'native/launcher.c'
            shutil.copy2(source, root / 'native/launcher.c')
            (root / 'scripts/launch.py').write_text('import json,sys; print(json.dumps(sys.argv[1:]))\n')
            executable = build_launcher(root)
            moved = root.with_name('moved portable 25%')
            root.rename(moved)
            executable = moved / 'AutoReserve'
            self.assertEqual(executable.read_bytes()[:4], b'\x7fELF')
            values = ['--config', 'config with spaces.json', 'a%F $literal \\ path', '中文']
            result = subprocess.run([str(executable), *values], cwd='/tmp', capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), ['-m', 'autoreserve', *values])

    # ==========================================
    # Function: Confirm desktop-entry escaping preserves the executable name rather than interpreting it.
    # Method: Launch the generated menu entry through GIO with a recording test executable.
    # ==========================================
    def test_desktop_exec_quoting_with_gio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'App "quoted" 75% $literal `tick` \\ 中文='
            root.mkdir()
            executable = root / 'AutoReserve'
            executable.write_text('#!/usr/bin/python3\nfrom pathlib import Path\nimport json,sys\nPath(__file__).with_name("result.json").write_text(json.dumps(sys.argv))\n')
            executable.chmod(0o755)
            with patch.dict(os.environ, {'XDG_DATA_HOME': str(Path(temporary) / 'xdg')}):
                entry = install_menu(root)
                self.assertIsNotNone(entry)
                old = entry.stat().st_mtime_ns
                self.assertEqual(install_menu(root).stat().st_mtime_ns, old)
            gio = C.CDLL('libgio-2.0.so.0')
            gio.g_desktop_app_info_new_from_filename.argtypes = [C.c_char_p]
            gio.g_desktop_app_info_new_from_filename.restype = C.c_void_p
            gio.g_app_info_launch.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]
            gio.g_app_info_launch.restype = C.c_int
            obj = C.CDLL('libgobject-2.0.so.0')
            obj.g_object_unref.argtypes = [C.c_void_p]
            app = gio.g_desktop_app_info_new_from_filename(os.fsencode(entry))
            self.assertTrue(app, entry.read_text())
            try:
                self.assertTrue(gio.g_app_info_launch(app, None, None, None))
                deadline = time.monotonic() + 5
                while not (root / 'result.json').exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(json.loads((root / 'result.json').read_text()), [str(executable)])
            finally:
                obj.g_object_unref(app)

    # ==========================================
    # Function: Preserve the sidebar's compact hierarchy after removing its obsolete tagline.
    # Method: Inspect the production builder layout and keep startup status out of booking requirements.
    # ==========================================
    def test_sidebar_tagline_removed_and_example_is_uninitialized(self):
        root = Path(__file__).resolve().parents[1]
        self.assertNotIn('安排好，交给它', (root / 'autoreserve/gtk_ui/main.ui').read_text())
        self.assertFalse(json.loads((root / 'config/example.json').read_text())['initialized'])
