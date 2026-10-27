"""Portable release allowlist, reproducibility, and metadata checks without network access."""

import hashlib
from pathlib import Path
import tarfile
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import zipfile

from tools.release import build,checksums,main,public_files,version


# ==========================================
# Function: Keep public GitHub artifacts independent of local credentials and runtime state.
# Method: Package isolated trees with deliberate private sentinels and inspect every archive member.
# ==========================================
class ReleaseTests(unittest.TestCase):
    # ==========================================
    # Function: Assemble a minimal public application with private data next to it.
    # Method: Create fixtures in a temporary application folder, never using the user's state.
    # ==========================================
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        public={'autoreserve/__init__.py':'__version__ = "1.2.3"\n','autoreserve/gtk_ui/main.ui':'<interface/>',
                'README.md':'Public program','LICENSE':'MIT','run.sh':'#!/bin/sh\n','config/example.json':'{}',
                '.github/workflows/release.yml':'CI', '.gitignore':'ignored', '.gitattributes':'attributes',
                'tests/test_private.py':'tests', 'demo/server.py':'demo', 'docs/WINDOWS.md':'docs',
                'native/windows-launcher.cs':'compiler input', 'native/windows-runtime.lock.json':'{}',
                'scripts/windows_launch.py':'runtime', 'scripts/bootstrap_windows.py':'runtime',
                'scripts/windows_smoke.py':'runtime', 'tools/windows_runtime.py':'runtime',
                'tools/build_windows.py':'builder', 'pyproject.toml':'build metadata'}
        private={'.auth/session.json':'SECRET-AUTH','config/config.json':'SECRET-CONFIG',
                 '.runtime/browsers/chrome':'SECRET-RUNTIME','.venv/lib/private.py':'SECRET-ENV',
                 'config/bootstrap.json':'SECRET-SETUP',
                 'config/legacy-config.json':'SECRET-OLD','config/artifacts/shot.png':'SECRET-IMAGE',
                 'artifacts/result.json':'SECRET-ORDER','autoreserve/__pycache__/x.pyc':'BYTECODE',
                 'personal.json':'PERSONAL'}
        for name,text in (public|private).items():
            path=self.root/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(text)

    # ==========================================
    # Function: Remove isolated package inputs and outputs after each check.
    # Method: Release temporary files without altering repository or user data.
    # ==========================================
    def tearDown(self):
        self.temp.cleanup()

    # ==========================================
    # Function: Exclude authentication, orders, screenshots and current/legacy configs from releases.
    # Method: Inspect both archive formats and their expanded bytes against private sentinels.
    # ==========================================
    def test_archives_exclude_private_data(self):
        assets=build(self.root,self.root/'dist')
        with tarfile.open(assets[0]) as archive:
            names=archive.getnames()
            payload=b''.join(archive.extractfile(m).read() for m in archive.getmembers() if m.isfile())
            self.assertIn('AutoReserve-1.2.3/autoreserve/gtk_ui/main.ui',names)
            self.assertNotIn('AutoReserve-1.2.3/config/example.json',names)
            self.assertIn('AutoReserve-1.2.3/.runtime/licenses/AutoReserve/LICENSE',names)
            self.assertEqual(archive.getmember('AutoReserve-1.2.3/run.sh').mode,0o755)
        with zipfile.ZipFile(assets[1]) as archive:
            self.assertEqual(set(archive.namelist()),set(names))
            self.assertEqual((archive.getinfo('AutoReserve-1.2.3/run.sh').external_attr>>16)&0o777,0o755)
        self.assertNotIn(b'SECRET',payload)
        self.assertNotIn(b'PERSONAL',payload)
        self.assertFalse(any('/.auth/' in n or '/artifacts/' in n or '__pycache__' in n for n in names))

    # ==========================================
    # Function: Exclude repository/build/test files and other-platform bootstraps from both portable payloads.
    # Method: Compare allowlisted paths against deliberately populated source-tree fixtures.
    # ==========================================
    def test_portable_payload_contains_only_platform_runtime(self):
        linux = {rel.as_posix() for rel, _ in public_files(self.root)}
        windows = {rel.as_posix() for rel, _ in public_files(self.root, target='windows')}
        self.assertIn('run.sh', linux)
        self.assertNotIn('run.sh', windows)
        self.assertIn('scripts/windows_launch.py', windows)
        self.assertIn('tools/windows_runtime.py', windows)
        self.assertIn('native/windows-runtime.lock.json', windows)
        self.assertNotIn('scripts/windows_launch.py', linux)
        for paths in (linux, windows):
            for name in ('README.md','LICENSE','.gitignore','.gitattributes','pyproject.toml',
                         '.github/workflows/release.yml','tests/test_private.py','demo/server.py',
                         'docs/WINDOWS.md','tools/build_windows.py','native/windows-launcher.cs'):
                self.assertNotIn(name, paths)

    # ==========================================
    # Function: Prevent removed files or prior user state from leaking through reused Windows staging.
    # Method: Build twice with fake private runtimes, seed obsolete files between builds and inspect the real ZIP.
    # ==========================================
    def test_windows_rebuild_cleans_staging(self):
        from tools import build_windows
        folder = self.root / '.runtime/windows-build/AutoReserve-1.2.3'

        # ==========================================
        # Function: Supply a minimal private interpreter without downloading or executing Windows artifacts.
        # Method: Write one marker under the same runtime directory as the production builder.
        # ==========================================
        def stage(root, cache):
            target = root / '.runtime/python/python.exe'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'MZ')

        # ==========================================
        # Function: Supply the native launcher marker at the compiler boundary.
        # Method: Keep the production copy and ZIP path while avoiding platform compiler dependencies.
        # ==========================================
        def compile_launcher(root):
            (root / 'AutoReserve.exe').write_bytes(b'MZ')

        with patch.object(build_windows,'ROOT',self.root), patch.object(build_windows,'stage',side_effect=stage), \
             patch.object(build_windows,'compile_launcher',side_effect=compile_launcher):
            build_windows.build()
            for name in ('README.md','.github/old.yml','tests/old.py','.auth/session.json','config/config.json'):
                path = folder / name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('STALE-PRIVATE')
            archive = build_windows.build()
        with zipfile.ZipFile(archive) as package:
            names = {name.removeprefix(folder.name+'/') for name in package.namelist()}
            self.assertIn('AutoReserve.exe',names)
            self.assertIn('.runtime/python/python.exe',names)
            self.assertIn('.runtime/licenses/AutoReserve/LICENSE',names)
            self.assertIn('scripts/windows_launch.py',names)
            self.assertFalse(any('STALE-PRIVATE' in package.read(name).decode(errors='ignore') for name in package.namelist()))
            self.assertNotIn('README.md',names)
            self.assertFalse((folder/'.github').exists())

    # ==========================================
    # Function: Produce reproducible archives and standard checksums.
    # Method: Build twice with the same inputs and compare exact SHA256 output bytes.
    # ==========================================
    def test_reproducible_archives_and_checksums(self):
        assets=build(self.root,self.root/'dist')
        before=[p.read_bytes() for p in assets]
        build(self.root,self.root/'dist')
        self.assertEqual(before,[p.read_bytes() for p in assets])
        text=checksums(self.root/'dist').read_text()
        for path in assets:
            self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name,text)
        self.assertEqual(version(self.root),'1.2.3')

    # ==========================================
    # Function: Refuse mismatched Git tags before building a public release.
    # Method: Pass a wrong expected tag and assert no output directory was created.
    # ==========================================
    def test_tag_mismatch_is_rejected(self):
        output=self.root/'should-not-exist'
        with self.assertRaises(ValueError):
            main(['--output',str(output),'--expected-tag','v0.0.0'])
        self.assertFalse(output.exists())

    # ==========================================
    # Function: Include the complete native UI and public release metadata in the package definition.
    # Method: Parse pyproject directly without installing a build environment.
    # ==========================================
    def test_package_declares_native_assets_and_mit(self):
        root=Path(__file__).resolve().parent.parent
        metadata=tomllib.loads((root/'pyproject.toml').read_text())
        self.assertEqual(metadata['project']['license'],'MIT')
        self.assertIn('gtk_ui/*.ui',metadata['tool']['setuptools']['package-data']['autoreserve'])
        self.assertIn('gtk_ui/*.css',metadata['tool']['setuptools']['package-data']['autoreserve'])
        self.assertEqual(metadata['project']['scripts']['autoreserve'],'autoreserve.__main__:entrypoint')
