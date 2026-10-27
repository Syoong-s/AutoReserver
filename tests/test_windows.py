"""Windows portability and local setup contract tests; no downloads or global installation."""

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from autoreserve.filelock import acquire, release
from autoreserve.native_runtime import library_path
from scripts.bootstrap_windows import WindowsBootstrap, atomic_json, environment, read_json, browser_available
from tools.windows_runtime import manifest, safe_member, stage_zip, gtk_payload, verified


# ==========================================
# Function: Check platform boundaries and first-run recovery without requiring Windows on the test runner.
# Method: Use temporary files plus strict mocks only at Windows CRT and subprocess boundaries.
# ==========================================
class WindowsTests(unittest.TestCase):
    # ==========================================
    # Function: Isolate native setup metadata, executable markers and user settings for each regression.
    # Method: Provide the real public lock manifest in a temporary portable folder.
    # ==========================================
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        spec = manifest()
        atomic_json(self.root / 'native/windows-runtime.lock.json', spec)
        self.setup = WindowsBootstrap(self.root, lambda _: None)

    # ==========================================
    # Function: Remove only the private fixture tree after releasing setup resources.
    # Method: Keep repository and host configuration out of temporary setup tests.
    # ==========================================
    def tearDown(self):
        self.temporary.cleanup()

    # ==========================================
    # Function: Reject corrupt dependency bytes and unsafe Windows ZIP names before extraction.
    # Method: Check hash/size pins, drive traversal, backslashes and ZIP symlinks.
    # ==========================================
    def test_download_integrity_and_archive_paths(self):
        payload = self.root / 'artifact'
        payload.write_bytes(b'verified')
        record = {'compressed_size':8, 'sha256':hashlib.sha256(b'verified').hexdigest()}
        self.assertTrue(verified(payload, record))
        payload.write_bytes(b'corrupt!')
        self.assertFalse(verified(payload, record))
        for name in ('../outside', '/absolute', 'D:/outside', 'pkg\\outside', 'pkg/../../outside'):
            with self.subTest(name=name), self.assertRaises(ValueError):safe_member(name)
        archive = self.root / 'unsafe.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('../outside', b'bad')
        with self.assertRaises(ValueError):stage_zip(archive, self.root / 'target')
        self.assertFalse((self.root / 'outside').exists())

    # ==========================================
    # Function: Include GTK runtime modules, schemas and licenses while excluding compilers and headers.
    # Method: Compare representative public MSYS2 paths against the release payload allowlist.
    # ==========================================
    def test_runtime_payload_excludes_development_tools(self):
        for name in ('bin/libgtk-3-0.dll', 'bin/gdk-pixbuf-query-loaders.exe',
                     'lib/gdk-pixbuf-2.0/2.10.0/loaders/libpixbufloader-svg.dll',
                     'share/licenses/gtk3/COPYING', 'share/glib-2.0/schemas/org.gtk.Settings.FileChooser.gschema.xml'):
            self.assertTrue(gtk_payload(PurePosixPath(name)), name)
        for name in ('include/gtk/gtk.h', 'bin/gcc.exe', 'bin/python.exe', 'lib/libgtk-3.dll.a',
                     'lib/gdk-pixbuf-2.0/2.10.0/loaders.cache'):
            self.assertFalse(gtk_payload(PurePosixPath(name)), name)

    # ==========================================
    # Function: Preserve wheel package layout without running a global pip installer.
    # Method: Extract normal and purelib members into one private module directory and skip generated scripts.
    # ==========================================
    def test_wheels_stage_only_into_private_destination(self):
        archive = self.root / 'fixture.whl'
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('example/__init__.py', 'value=1')
            package.writestr('example.data/purelib/native.pyd', b'binary')
            package.writestr('example.data/scripts/install.exe', b'not a runtime module')
        target = self.root / '.runtime/site-packages'
        stage_zip(archive, target, wheel=True)
        self.assertTrue((target / 'example/__init__.py').is_file())
        self.assertTrue((target / 'native.pyd').is_file())
        self.assertFalse((target / 'install.exe').exists())

    # ==========================================
    # Function: Keep every Windows cache and DLL lookup app-local without mutating the parent environment.
    # Method: Inject hostile inherited Python settings and inspect the returned child paths.
    # ==========================================
    def test_environment_is_private_and_relocatable(self):
        with patch.dict('os.environ', {'PYTHONHOME':'foreign', 'PYTHONPATH':'foreign', 'WINDIR':'Windows'}):
            env = environment(self.root)
            import os
            self.assertEqual(os.environ['PYTHONHOME'], 'foreign')
        self.assertNotIn('PYTHONHOME', env)
        self.assertNotIn('PYTHONPATH', env)
        for name in ('PLAYWRIGHT_BROWSERS_PATH', 'TEMP', 'TMP', 'GSETTINGS_SCHEMA_DIR', 'FONTCONFIG_FILE'):
            self.assertTrue(Path(env[name]).is_relative_to(self.root), name)
        self.assertEqual(env['GDK_BACKEND'], 'win32')
        self.assertEqual(Path(library_path('gtk', windows=True)).parts[-4:], ('.runtime','gtk','bin','libgtk-3-0.dll'))
        self.assertEqual(library_path('gtk', windows=False), 'libgtk-3.so.0')

    # ==========================================
    # Function: Keep exact wheel/embedded interpreter pins compatible with the production reservation code.
    # Method: Inspect the checked-in manifest for the GIL-enabled amd64 ABI, timezone data and complete hashes.
    # ==========================================
    def test_manifest_has_windows_abi_timezone_and_hashes(self):
        spec = manifest()
        self.assertEqual(spec['target'], 'windows-x86_64')
        self.assertEqual(spec['python']['version'], '3.14.5')
        wheels = {item['name']:item for item in spec['wheels']}
        self.assertIn('tzdata', wheels)
        self.assertIn('cp314-cp314-win_amd64', wheels['greenlet']['filename'])
        for item in [spec['python'], *spec['wheels'], *spec['gtk']['packages']]:
            self.assertEqual(len(item['sha256']), 64)
            self.assertTrue(item['url'].startswith('https://'))

    # ==========================================
    # Function: Exclude concurrent Windows workers using one persistent byte without changing journal identity.
    # Method: Verify actual offsets and lock/unlock calls through the platform-specific CRT boundary.
    # ==========================================
    def test_windows_lock_keeps_a_stable_byte(self):
        handle = io.BytesIO()
        handle.fileno = lambda: 7
        crt = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=Mock())
        with patch('autoreserve.filelock.os', SimpleNamespace(name='nt')), patch.dict('sys.modules', {'msvcrt':crt}):
            acquire(handle)
            self.assertEqual(handle.getvalue(), b'\0')
            self.assertEqual(handle.tell(), 0)
            release(handle)
            crt.locking.side_effect = OSError(22, 'unsupported filesystem')
            with self.assertRaisesRegex(RuntimeError, 'Windows 本地磁盘'):
                acquire(handle)
        self.assertEqual(crt.locking.call_args_list[0].args, (7,2,1))
        self.assertEqual(crt.locking.call_args_list[1].args, (7,0,1))

    # ==========================================
    # Function: Treat broken setup receipts and executable markers as incomplete initialization.
    # Method: Reject directories, missing files and non-PE data while keeping JSON parsing defensive.
    # ==========================================
    def test_receipt_and_browser_markers_are_not_trusted(self):
        self.assertEqual(read_json(self.root / 'missing.json'), {})
        atomic_json(self.root / 'state.json', [])
        self.assertEqual(read_json(self.root / 'state.json'), {})
        self.assertFalse(browser_available(None))
        file = self.root / 'chrome.exe'
        file.write_bytes(b'not-a-browser' * 10)
        self.assertFalse(browser_available(file))
        file.write_bytes(b'MZ' + b'\0' * 100)
        self.assertTrue(browser_available(file))

    # ==========================================
    # Function: Preserve user goals and allow another attempt after Windows dependency setup fails.
    # Method: Fail the locked operation, inspect false flags and then let the next verified attempt succeed.
    # ==========================================
    def test_setup_failure_preserves_user_config_and_is_retryable(self):
        atomic_json(self.setup.config_path, {'initialized':True, 'goals':[{'id':'keep-me'}]})
        with patch.object(self.setup, 'ensure_locked', side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError, 'offline'):self.setup.ensure()
        self.assertFalse(read_json(self.setup.receipt)['initialized'])
        saved = read_json(self.setup.config_path)
        self.assertFalse(saved['initialized'])
        self.assertEqual(saved['goals'], [{'id':'keep-me'}])
        with patch.object(self.setup, 'ensure_locked', return_value='ready'):
            self.assertEqual(self.setup.ensure(), 'ready')
