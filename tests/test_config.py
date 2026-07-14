# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.config."""

import json
import os
import unittest

from nanvix_zutil.config import (
    Config,
    DeploymentMode,
    Host,
    Machine,
    MemorySize,
    Target,
)
from nanvix_zutil.paths import nanvix_root


class TestConfigEnums(unittest.TestCase):
    """Enum-typed properties preserve str compatibility."""

    def _clean(self) -> None:
        for key in (
            "NANVIX_HOST",
            "NANVIX_TARGET",
            "NANVIX_MACHINE",
            "NANVIX_DEPLOYMENT_MODE",
            "NANVIX_MEMORY_SIZE",
        ):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._clean()

    def test_host_default_matches_platform(self) -> None:
        import sys

        self._clean()
        cfg = Config()
        expected = Host.windows if sys.platform == "win32" else Host.linux
        self.assertEqual(cfg.host, expected)

    def test_properties_return_enums(self) -> None:
        self._clean()
        cfg = Config()
        self.assertIsInstance(cfg.host, Host)
        self.assertIsInstance(cfg.target, Target)
        self.assertIsInstance(cfg.machine, Machine)
        self.assertIsInstance(cfg.deployment_mode, DeploymentMode)
        self.assertIsInstance(cfg.memory_size, MemorySize)

    def test_str_equality_and_fstring(self) -> None:
        self._clean()
        cfg = Config()
        self.assertEqual(cfg.machine, "microvm")
        self.assertEqual(f"{cfg.machine}", "microvm")

    def test_env_override_still_produces_enum(self) -> None:
        # Host is the only remaining enum with more than one value.
        # Force the non-platform-default so this exercises an actual override.
        import sys

        other = Host.linux if sys.platform == "win32" else Host.windows
        os.environ["NANVIX_HOST"] = other.value
        cfg = Config()
        self.assertIs(cfg.host, other)


class TestConfigValidation(unittest.TestCase):
    """Invalid enum values are fatal at construction time."""

    _KEYS = (
        "NANVIX_HOST",
        "NANVIX_TARGET",
        "NANVIX_MACHINE",
        "NANVIX_DEPLOYMENT_MODE",
        "NANVIX_MEMORY_SIZE",
    )

    def tearDown(self) -> None:
        for key in self._KEYS:
            os.environ.pop(key, None)

    def test_invalid_value_is_fatal_for_every_enum_key(self) -> None:
        for key in self._KEYS:
            with self.subTest(key=key):
                os.environ[key] = "not-a-real-value"
                with self.assertRaises(SystemExit):
                    Config()
                os.environ.pop(key, None)


class TestConfigDefaults(unittest.TestCase):
    """Config initialises with built-in defaults."""

    def _make_config(self) -> Config:
        # Ensure env vars don't interfere with default tests.
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)
        return Config()

    def test_default_machine(self) -> None:
        cfg = self._make_config()
        self.assertEqual(cfg.machine, "microvm")

    def test_default_deployment_mode(self) -> None:
        cfg = self._make_config()
        self.assertEqual(cfg.deployment_mode, "standalone")

    def test_default_memory_size(self) -> None:
        cfg = self._make_config()
        self.assertEqual(cfg.memory_size, "256mb")


class TestConfigEnvOverride(unittest.TestCase):
    """Environment variables override defaults and persisted values."""

    def tearDown(self) -> None:
        os.environ.pop("NANVIX_MACHINE", None)

    def test_env_overrides_default(self) -> None:
        os.environ["NANVIX_MACHINE"] = "microvm"
        cfg = Config()
        self.assertEqual(cfg.machine, "microvm")


class TestConfigPersistence(unittest.TestCase):
    """Config.save() / load() round-trip."""

    def setUp(self) -> None:
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def test_save_creates_file(self) -> None:
        cfg = Config()
        cfg.save()
        config_path = nanvix_root() / "env.json"
        self.assertTrue(config_path.exists())

    def test_round_trip(self) -> None:
        cfg = Config()
        cfg.set("NANVIX_SYSROOT", "/some/path")
        cfg.save()

        cfg2 = Config()
        self.assertEqual(cfg2.get("NANVIX_SYSROOT"), "/some/path")

    def test_load_ignores_malformed_json(self) -> None:
        (nanvix_root() / "env.json").write_text("not json", encoding="utf-8")
        # Should not raise.
        cfg = Config()
        self.assertEqual(cfg.machine, "microvm")

    def test_load_ignores_non_dict_json(self) -> None:
        (nanvix_root() / "env.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        cfg = Config()
        self.assertEqual(cfg.machine, "microvm")


class TestConfigGetSet(unittest.TestCase):
    """Config.get() and Config.set() work correctly."""

    def setUp(self) -> None:
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def test_get_missing_key_returns_default(self) -> None:
        cfg = Config()
        self.assertIsNone(cfg.get("NONEXISTENT_KEY"))
        self.assertEqual(cfg.get("NONEXISTENT_KEY", "fallback"), "fallback")

    def test_get_with_str_default_returns_str(self) -> None:
        cfg = Config()
        # When a str default is given, the return value must be str (not None).
        result = cfg.get("NONEXISTENT_KEY", "/opt/nanvix")
        self.assertIsInstance(result, str)
        self.assertEqual(result, "/opt/nanvix")

    def test_set_then_get(self) -> None:
        cfg = Config()
        cfg.set("MY_KEY", "my_value")
        self.assertEqual(cfg.get("MY_KEY"), "my_value")

    def test_delete_removes_key(self) -> None:
        cfg = Config()
        cfg.set("MY_KEY", "my_value")
        cfg.delete("MY_KEY")
        self.assertIsNone(cfg.get("MY_KEY"))

    def test_delete_missing_key_is_noop(self) -> None:
        cfg = Config()
        cfg.delete("NONEXISTENT_KEY")  # should not raise

    def test_delete_persists_after_save(self) -> None:
        cfg = Config()
        cfg.set("MY_KEY", "my_value")
        cfg.save()
        cfg.delete("MY_KEY")
        cfg.save()
        cfg2 = Config()
        self.assertIsNone(cfg2.get("MY_KEY"))


if __name__ == "__main__":
    unittest.main()
