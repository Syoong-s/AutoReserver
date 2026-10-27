"""Application-relative configuration and non-destructive legacy migration tests."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autoreserve.paths import default_config_path,default_auth_dir,prepare_config
from autoreserve.config import load_config,save_config
from test_catalog import native_config


# ==========================================
# Function: Keep config paths stable across working directories and preserve existing preferences.
# Method: Exercise the resolver/migration using isolated application folders.
# ==========================================
class ConfigPathTests(unittest.TestCase):
    # ==========================================
    # Function: Resolve defaults beneath the program folder rather than the caller's current directory.
    # Method: Substitute an application root and inspect both fixed config and account paths.
    # ==========================================
    def test_defaults_are_application_relative(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch('autoreserve.paths.application_dir',return_value=root):
                self.assertEqual(default_config_path(),root/'config/config.json')
                self.assertEqual(default_auth_dir(),root/'.auth')

    # ==========================================
    # Function: Move a valid old config only after its replacement is durable.
    # Method: Compare restored goals and retain the original JSON beneath the config folder.
    # ==========================================
    def test_migration_preserves_old_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            source=native_config()
            save_config(source,root/'config.json')
            original=(root/'config.json').read_bytes()
            destination=prepare_config(root/'config/config.json',root)
            self.assertEqual(load_config(destination),source)
            self.assertFalse((root/'config.json').exists())
            self.assertEqual((root/'config/legacy-config.json').read_bytes(),original)
            self.assertEqual(destination.stat().st_mode&0o777,0o600)

    # ==========================================
    # Function: Never replace a new config with a leftover older root-level file.
    # Method: Provide both files and verify the configured target remains authoritative.
    # ==========================================
    def test_existing_target_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            old,new=native_config(),native_config()
            new.poll_interval_seconds=29
            save_config(old,root/'config.json')
            save_config(new,root/'config/config.json')
            prepare_config(root/'config/config.json',root)
            self.assertEqual(load_config(root/'config/config.json').poll_interval_seconds,29)
            self.assertTrue((root/'config.json').exists())

    # ==========================================
    # Function: Keep damaged old configs recoverable and explicit test paths isolated.
    # Method: Reject bad JSON before creating a target and leave explicit overrides unchanged.
    # ==========================================
    def test_invalid_source_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'config.json').write_text('broken')
            with self.assertRaises(ValueError):
                prepare_config(root/'config/config.json',root)
            self.assertFalse((root/'config/config.json').exists())
            override=root/'private-test.json'
            self.assertEqual(prepare_config(override,root),override)
            self.assertTrue((root/'config.json').exists())
