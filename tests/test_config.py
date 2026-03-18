# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.config."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from nanvix_zutil.config import Config


class TestConfigDefaults(unittest.TestCase):
    """Config initialises with built-in defaults."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._nanvix_dir = Path(self._tmpdir.name) / ".nanvix"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_config(self) -> Config:
        # Ensure env vars don't interfere with default tests.
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)
        return Config(self._nanvix_dir)

    def test_default_machine(self) -> None:
        cfg = self._make_config()
        self.assertEqual(cfg.machine, "hyperlight")

    def test_default_deployment_mode(self) -> None:
        cfg = self._make_config()
        self.assertEqual(cfg.deployment_mode, "multi-process")

    def test_default_memory_size(self) -> None:
        cfg = self._make_config()
        self.assertEqual(cfg.memory_size, "128mb")


class TestConfigEnvOverride(unittest.TestCase):
    """Environment variables override defaults and persisted values."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._nanvix_dir = Path(self._tmpdir.name) / ".nanvix"

    def tearDown(self) -> None:
        os.environ.pop("NANVIX_MACHINE", None)
        self._tmpdir.cleanup()

    def test_env_overrides_default(self) -> None:
        os.environ["NANVIX_MACHINE"] = "microvm"
        cfg = Config(self._nanvix_dir)
        self.assertEqual(cfg.machine, "microvm")


class TestConfigPersistence(unittest.TestCase):
    """Config.save() / load() round-trip."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_save_creates_file(self) -> None:
        cfg = Config(self._nanvix_dir)
        cfg.save()
        config_path = self._nanvix_dir / "env.json"
        self.assertTrue(config_path.exists())

    def test_round_trip(self) -> None:
        cfg = Config(self._nanvix_dir)
        cfg.set("NANVIX_SYSROOT", "/some/path")
        cfg.save()

        cfg2 = Config(self._nanvix_dir)
        self.assertEqual(cfg2.get("NANVIX_SYSROOT"), "/some/path")

    def test_load_ignores_malformed_json(self) -> None:
        self._nanvix_dir.mkdir(parents=True, exist_ok=True)
        (self._nanvix_dir / "env.json").write_text("not json", encoding="utf-8")
        # Should not raise.
        cfg = Config(self._nanvix_dir)
        self.assertEqual(cfg.machine, "hyperlight")

    def test_load_ignores_non_dict_json(self) -> None:
        self._nanvix_dir.mkdir(parents=True, exist_ok=True)
        (self._nanvix_dir / "env.json").write_text(
            json.dumps([1, 2, 3]), encoding="utf-8"
        )
        cfg = Config(self._nanvix_dir)
        self.assertEqual(cfg.machine, "hyperlight")


class TestConfigGetSet(unittest.TestCase):
    """Config.get() and Config.set() work correctly."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_get_missing_key_returns_default(self) -> None:
        cfg = Config(self._nanvix_dir)
        self.assertIsNone(cfg.get("NONEXISTENT_KEY"))
        self.assertEqual(cfg.get("NONEXISTENT_KEY", "fallback"), "fallback")

    def test_set_then_get(self) -> None:
        cfg = Config(self._nanvix_dir)
        cfg.set("MY_KEY", "my_value")
        self.assertEqual(cfg.get("MY_KEY"), "my_value")


if __name__ == "__main__":
    unittest.main()
