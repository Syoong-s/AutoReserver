"""Portable release allowlist, reproducibility, and metadata checks without network access."""

import hashlib
from pathlib import Path
import tarfile
import tempfile
import tomllib
import unittest
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
                'README.md':'Public program','LICENSE':'MIT','run.sh':'#!/bin/sh\n','config/example.json':'{}'}
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
            self.assertIn('AutoReserve-1.2.3/config/example.json',names)
            self.assertEqual(archive.getmember('AutoReserve-1.2.3/run.sh').mode,0o755)
        with zipfile.ZipFile(assets[1]) as archive:
            self.assertEqual(set(archive.namelist()),set(names))
            self.assertEqual((archive.getinfo('AutoReserve-1.2.3/run.sh').external_attr>>16)&0o777,0o755)
        self.assertNotIn(b'SECRET',payload)
        self.assertNotIn(b'PERSONAL',payload)
        self.assertFalse(any('/.auth/' in n or '/artifacts/' in n or '__pycache__' in n for n in names))

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
