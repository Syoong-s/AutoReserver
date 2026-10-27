"""First-run state, repair, isolation and retry regressions without network access."""

import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from autoreserve.config import Config, config_from_dict
from scripts.bootstrap import Bootstrap, atomic_json, environment, read_receipt
from scripts.bootstrap_native import NativeInstaller, native_environment


# ==========================================
# Function: Validate app-local dependency setup without installing into the test runner's environment.
# Method: Use isolated directories and replace external tools at their subprocess boundary.
# ==========================================
class BootstrapTests(unittest.TestCase):
    # ==========================================
    # Function: Create a minimal application folder with simulated browser executable files.
    # Method: Keep initialization flags and private booking data confined to one temporary tree.
    # ==========================================
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'requirements.txt').write_text('playwright==1.64.1\n')
        self.browser = self.root / '.runtime/browsers/chrome'
        self.headless = self.root / '.runtime/browsers/headless_shell'
        self.browser.parent.mkdir(parents=True)
        for file in [self.browser, self.headless]:
            file.write_bytes(b'\x7fELF')
            file.chmod(0o755)
        self.setup = Bootstrap(self.root, lambda message: None)
        self.info = {'version': [3, 12, 13], 'browser': str(self.browser), 'headless': str(self.headless)}
        self.probe = patch('scripts.bootstrap.probe', return_value=self.info).start()
        self.gtk = patch('scripts.bootstrap.gtk_available', return_value=True).start()
        self.libraries = patch('scripts.bootstrap.missing_libraries', return_value=[]).start()
        self.which = patch('scripts.bootstrap.shutil.which', return_value=None).start()

    # ==========================================
    # Function: Release all setup fixtures and mocked tools after each test.
    # Method: Restore subprocess boundaries and remove only the temporary application tree.
    # ==========================================
    def tearDown(self):
        patch.stopall()
        self.temp.cleanup()

    # ==========================================
    # Function: Record initialized=true only after the browser and config validation succeed.
    # Method: Simulate successful durable saving and require the next launch to skip all installers.
    # ==========================================
    def test_success_then_offline_fast_launch(self):
        # ==========================================
        # Function: Simulate a durable initialized config without invoking another interpreter.
        # Method: Preserve a booking goal while writing the isolated fixture atomically.
        # ==========================================
        def save_config():
            atomic_json(self.setup.config_path, {'initialized': True, 'goals': [{'name': 'keep me'}]})
        with patch.object(self.setup, 'run') as run, patch.object(self.setup, 'mark_config', side_effect=save_config), \
             patch.object(self.setup, 'install_python') as install:
            self.setup.ensure()
            self.assertTrue(read_receipt(self.setup.state_path)['initialized'])
            self.assertTrue(read_receipt(self.setup.config_path)['initialized'])
            self.assertEqual(run.call_count, 1)
            run.reset_mock()
            self.setup.ensure()
            run.assert_not_called()
            install.assert_not_called()

    # ==========================================
    # Function: Recover from deleted headless-browser files even when the initialization flag is true.
    # Method: Remove only the shell, require a Chromium repair command and verify a fresh receipt.
    # ==========================================
    def test_missing_browser_is_repaired(self):
        self.headless.unlink()
        actions = []
        # ==========================================
        # Function: Simulate only the missing headless-browser download.
        # Method: Record real argument boundaries and restore the executable fixture.
        # ==========================================
        def command(argv, message):
            actions.append(list(map(str, argv)))
            if 'install' in argv:
                self.headless.write_bytes(b'\x7fELF')
                self.headless.chmod(0o755)
        with patch.object(self.setup, 'run', side_effect=command), patch.object(self.setup, 'mark_config'):
            self.setup.ensure()
        self.assertTrue(any(argv[-4:] == ['playwright', 'install', '--force', 'chromium'] for argv in actions))
        self.assertTrue(read_receipt(self.setup.state_path)['initialized'])

    # ==========================================
    # Function: Retain booking preferences and allow retry after an interrupted installation.
    # Method: Fail browser verification, inspect false flags, then complete setup on the next launch.
    # ==========================================
    def test_failure_keeps_goals_and_retry_succeeds(self):
        atomic_json(self.setup.config_path, {'initialized': True, 'goals': [{'id': 'private-goal'}]})
        with patch.object(self.setup, 'run', side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                self.setup.ensure()
        self.assertFalse(read_receipt(self.setup.state_path)['initialized'])
        saved = read_receipt(self.setup.config_path)
        self.assertFalse(saved['initialized'])
        self.assertEqual(saved['goals'], [{'id': 'private-goal'}])
        with patch.object(self.setup, 'run'), patch.object(self.setup, 'mark_config'):
            self.setup.ensure()
        self.assertTrue(read_receipt(self.setup.state_path)['initialized'])

    # ==========================================
    # Function: Repair a mismatched Python ABI instead of trusting an old initialized receipt.
    # Method: Fail the first live package probe and require the isolated venv installer.
    # ==========================================
    def test_package_mismatch_repairs_local_environment(self):
        self.probe.side_effect = [None, self.info]
        local_python = self.root / '.venv/bin/python'
        with patch.object(self.setup, 'install_python', return_value=local_python) as install, \
             patch.object(self.setup, 'run'), patch.object(self.setup, 'mark_config'):
            selected, env, info = self.setup.ensure()
        install.assert_called_once()
        self.assertEqual(selected, str(local_python))

    # ==========================================
    # Function: Never execute a damaged downloaded dependency installer.
    # Method: Return an archive with the wrong checksum and assert no executable was created.
    # ==========================================
    def test_uv_checksum_failure_never_executes_download(self):
        with patch('scripts.bootstrap.urllib.request.urlopen', return_value=io.BytesIO(b'corrupt')), \
             patch('scripts.bootstrap.platform.machine', return_value='x86_64'):
            with self.assertRaisesRegex(RuntimeError, '校验'):
                self.setup.uv()
        self.assertFalse((self.root / '.runtime/bin/uv').exists())

    # ==========================================
    # Function: Prevent two mouse launches from modifying the same dependency tree concurrently.
    # Method: Hold the real advisory setup lock, start a waiter and release it after observing the wait.
    # ==========================================
    def test_concurrent_launch_waits_for_local_lock(self):
        waiting = threading.Event()
        self.setup.progress = lambda message: waiting.set() if '等待' in message else None
        with (self.root / '.runtime/setup.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with patch.object(self.setup, '_ensure_locked', return_value='done') as work:
                thread = threading.Thread(target=self.setup.ensure)
                thread.start()
                self.assertTrue(waiting.wait(2))
                work.assert_not_called()
                fcntl.flock(lock, fcntl.LOCK_UN)
                thread.join(2)
                self.assertFalse(thread.is_alive())
                work.assert_called_once()

    # ==========================================
    # Function: Keep caches, browsers, Python downloads and environment search paths app-local.
    # Method: Inject hostile inherited overrides and verify neither globals nor the caller are modified.
    # ==========================================
    def test_environment_is_local_and_does_not_mutate_parent(self):
        with patch.dict(os.environ, {'PYTHONPATH': '/foreign/python', 'PYTHONHOME': '/foreign/home',
                                     'VIRTUAL_ENV': '/foreign/venv', 'LD_LIBRARY_PATH': '/foreign/libs',
                                     'UV_SYSTEM_PYTHON': '1'}):
            env = environment(self.root)
            self.assertEqual(os.environ['PYTHONHOME'], '/foreign/home')
        for key in ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'UV_SYSTEM_PYTHON']:
            self.assertNotIn(key, env)
        for key in ['UV_CACHE_DIR', 'UV_PYTHON_INSTALL_DIR', 'UV_PYTHON_BIN_DIR', 'UV_TOOL_DIR',
                    'PLAYWRIGHT_BROWSERS_PATH', 'XDG_CACHE_HOME', 'TMPDIR']:
            self.assertTrue(Path(env[key]).is_relative_to(self.root), key)
        self.assertNotIn('/foreign/libs', env['LD_LIBRARY_PATH'])

    # ==========================================
    # Function: Download native dependencies without invoking sudo, apt installation or dpkg installation.
    # Method: Inspect the exact apt redirections and extraction argv emitted by a rootless repair.
    # ==========================================
    def test_native_download_never_installs_globally(self):
        actions = []
        # ==========================================
        # Function: Stage one fake downloaded package without executing apt.
        # Method: Record the generated command and provide an archive for the extraction stage.
        # ==========================================
        def command(argv, message):
            actions.append(list(map(str, argv)))
            if '--download-only' in argv:
                cache = self.root / '.runtime/apt/cache/archives'
                (cache / 'libexample_1_amd64.deb').touch()
        installer = NativeInstaller(self.root, command)
        with patch('scripts.bootstrap_native.shutil.which', return_value='/usr/bin/tool'):
            installer.install(['libgtk-3-0t64'])
        apt = [a for a in actions if a[0] == 'apt-get']
        self.assertEqual(len(apt), 2)
        self.assertIn('--download-only', apt[1])
        self.assertIn('Dir::State::status=/var/lib/dpkg/status', apt[1])
        self.assertIn('#clear APT::Update::Post-Invoke;', (self.root / '.runtime/apt/no-hooks.conf').read_text())
        for setting in ('Dir::State=', 'Dir::Cache=', 'Dir::Log='):
            value = next(v for v in apt[1] if v.startswith(setting))
            self.assertTrue(Path(value.split('=', 1)[1]).is_relative_to(self.root))
        self.assertEqual(actions[-1][0:2], ['dpkg-deb', '--extract'])
        self.assertFalse(any('sudo' in a or '-i' in a for a in actions))

    # ==========================================
    # Function: Restore setup after receipt corruption without touching booking requirements.
    # Method: Treat non-object or invalid JSON as incomplete state and keep the public schema strict.
    # ==========================================
    def test_receipt_corruption_and_initialized_schema(self):
        self.setup.state_path.parent.mkdir(exist_ok=True)
        self.setup.state_path.write_text('broken')
        self.assertEqual(read_receipt(self.setup.state_path), {})
        self.assertFalse(Config().initialized)
        self.assertTrue(config_from_dict({'initialized': True}).initialized)
        with self.assertRaisesRegex(ValueError, 'initialized'):
            config_from_dict({'initialized': 'yes'})
