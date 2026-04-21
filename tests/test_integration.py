# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Integration tests for nanvix_zutil.

These tests exercise the full lifecycle of a mock consumer ``z.py`` that
subclasses :class:`~nanvix_zutil.ZScript` and implements all lifecycle hooks.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nanvix_zutil.log as log_mod
from nanvix_zutil import ZScript
from tests.testutils import write_manifest

# ---------------------------------------------------------------------------
# Mock consumer
# ---------------------------------------------------------------------------


class _MockConsumer(ZScript):
    """Mock consumer that records which lifecycle hooks are called."""

    def __init__(self, repo_root: Path) -> None:
        super().__init__(repo_root)
        self.called: list[str] = []

    def setup(self) -> bool:
        """Record setup hook invocation."""
        self.called.append("setup")
        return False

    def build(self) -> None:
        """Record build hook invocation."""
        self.called.append("build")

    def test(self) -> None:
        """Record test hook invocation."""
        self.called.append("test")

    def benchmark(self) -> None:
        """Record benchmark hook invocation."""
        self.called.append("benchmark")

    def release(self) -> None:
        """Record release hook invocation."""
        self.called.append("release")

    def clean(self) -> None:
        """Record clean hook invocation."""
        self.called.append("clean")

    def distclean(self) -> None:
        """Record distclean hook invocation."""
        self.called.append("distclean")


class TestIntegrationLifecycle(unittest.TestCase):
    """Full lifecycle dispatch through ZScript.main()."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._repo_root = Path(self._tmpdir.name)
        write_manifest(self._repo_root)
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def _run_main(
        self, subcommand: str, extra_argv: list[str] | None = None
    ) -> _MockConsumer:
        """Run _MockConsumer.main() with *subcommand* and return the instance.

        Patches ``sys.argv`` and captures the created instance by
        temporarily monkeypatching the class constructor.
        """
        # z.py lives at <repo>/.nanvix/z.py — script_path.parent.name == ".nanvix"
        fake_script = str(self._repo_root / ".nanvix" / "z.py")
        argv = [fake_script, subcommand] + (extra_argv or [])

        created: list[_MockConsumer] = []
        original_init = _MockConsumer.__init__

        def capturing_init(self_: _MockConsumer, repo_root: Path) -> None:
            original_init(self_, repo_root)
            created.append(self_)

        with (
            patch.object(_MockConsumer, "__init__", capturing_init),
            patch("sys.argv", argv),
        ):
            _MockConsumer.main()

        return created[0]

    def test_setup_hook_called(self) -> None:
        instance = self._run_main("setup")
        self.assertIn("setup", instance.called)

    def test_build_hook_called(self) -> None:
        instance = self._run_main("build")
        self.assertIn("build", instance.called)

    def test_test_hook_called(self) -> None:
        instance = self._run_main("test")
        self.assertIn("test", instance.called)

    def test_benchmark_hook_called(self) -> None:
        instance = self._run_main("benchmark")
        self.assertIn("benchmark", instance.called)

    def test_release_hook_called(self) -> None:
        instance = self._run_main("release")
        self.assertIn("release", instance.called)

    def test_clean_hook_called(self) -> None:
        instance = self._run_main("clean")
        self.assertIn("clean", instance.called)

    def test_distclean_hook_called(self) -> None:
        instance = self._run_main("distclean")
        self.assertIn("distclean", instance.called)

    def test_json_flag_enables_json_mode(self) -> None:
        """--json flag produces JSON output on stderr."""
        from io import StringIO

        fake_script = str(self._repo_root / ".nanvix" / "z.py")
        # build hook records "build" but doesn't log — capture info from run()
        # Instead verify that the --json flag propagates by checking log output
        # of a info call that happens internally (e.g. no-op build emits nothing,
        # but we can check the mode was set by attempting a direct log call).
        with patch("sys.argv", [fake_script, "--json", "build"]):
            _MockConsumer.main()

        # After main(), log mode was set to True. Verify it produces JSON output.
        buf = StringIO()
        original_stderr = sys.stderr
        sys.stderr = buf
        try:
            log_mod.info("json-check")
        finally:
            sys.stderr = original_stderr
            log_mod.set_json_mode(False)

        raw: object = json.loads(buf.getvalue().strip())
        self.assertIsInstance(raw, dict)
        assert isinstance(raw, dict)
        self.assertEqual(raw["level"], "info")

    def test_help_subcommand_returns(self) -> None:
        """help subcommand prints help and returns without calling any hook."""
        fake_script = str(self._repo_root / ".nanvix" / "z.py")
        with patch("sys.argv", [fake_script, "help"]):
            # Should not raise.
            _MockConsumer.main()

    def test_no_subcommand_returns(self) -> None:
        """Missing subcommand prints help and returns without calling any hook."""
        fake_script = str(self._repo_root / ".nanvix" / "z.py")
        with patch("sys.argv", [fake_script]):
            _MockConsumer.main()

    def test_help_without_manifest_does_not_exit_with_missing_dep(self) -> None:
        """'help' works even when nanvix.toml is absent (no EXIT_MISSING_DEP)."""
        import tempfile

        with tempfile.TemporaryDirectory() as empty_dir:
            fake_script = str(Path(empty_dir) / ".nanvix" / "z.py")
            with patch("sys.argv", [fake_script, "help"]):
                # Must not raise SystemExit(3) or any other error.
                _MockConsumer.main()

    def test_no_subcommand_without_manifest_does_not_exit_with_missing_dep(
        self,
    ) -> None:
        """No-subcommand invocation works even when nanvix.toml is absent."""
        import tempfile

        with tempfile.TemporaryDirectory() as empty_dir:
            fake_script = str(Path(empty_dir) / ".nanvix" / "z.py")
            with patch("sys.argv", [fake_script]):
                _MockConsumer.main()

    def test_repo_root_inferred_from_nanvix_dir(self) -> None:
        """Repo root is the parent of the .nanvix/ directory."""
        fake_script = str(self._repo_root / ".nanvix" / "z.py")

        captured: list[Path] = []
        original_init = _MockConsumer.__init__

        def capturing_init(self_: _MockConsumer, repo_root: Path) -> None:
            original_init(self_, repo_root)
            captured.append(repo_root)

        with (
            patch.object(_MockConsumer, "__init__", capturing_init),
            patch("sys.argv", [fake_script, "build"]),
        ):
            _MockConsumer.main()

        self.assertEqual(captured[0], self._repo_root.resolve())

    def test_config_defaults_accessible(self) -> None:
        """Config is accessible from the script and has expected defaults."""
        instance = self._run_main("setup")
        self.assertEqual(instance.config.machine, "microvm")
        self.assertEqual(instance.config.deployment_mode, "standalone")
        self.assertEqual(instance.config.memory_size, "256mb")

    def test_targets_empty_by_default(self) -> None:
        """Without --, targets should be an empty list."""
        instance = self._run_main("build")
        self.assertEqual(instance.targets, [])

    def test_targets_passed_after_double_dash(self) -> None:
        """Arguments after -- are available as instance.targets."""
        instance = self._run_main("test", extra_argv=["--", "smoke", "integration"])
        self.assertEqual(instance.targets, ["smoke", "integration"])

    def test_targets_single_value(self) -> None:
        """A single target after -- is a one-element list."""
        instance = self._run_main("build", extra_argv=["--", "all"])
        self.assertEqual(instance.targets, ["all"])


class TestIntegrationConfigPersistence(unittest.TestCase):
    """Config.save() / load() round-trip via ZScript."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._repo_root = Path(self._tmpdir.name)
        write_manifest(self._repo_root)
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_config_save_and_reload(self) -> None:
        script = _MockConsumer(self._repo_root)
        script.config.set("NANVIX_SYSROOT", "/tmp/sysroot")
        script.config.save()

        # A fresh instance should see the persisted value.
        script2 = _MockConsumer(self._repo_root)
        self.assertEqual(script2.config.get("NANVIX_SYSROOT"), "/tmp/sysroot")


class TestIntegrationRunSubprocess(unittest.TestCase):
    """ZScript.run() executes subprocesses and propagates errors."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._repo_root = Path(self._tmpdir.name)
        write_manifest(self._repo_root)
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_run_echo_succeeds(self) -> None:
        script = _MockConsumer(self._repo_root)
        result = script.run(sys.executable, "-c", "import sys; sys.exit(0)")
        self.assertEqual(result.returncode, 0)

    def test_run_exit_nonzero_raises_system_exit_5(self) -> None:
        script = _MockConsumer(self._repo_root)
        log_mod.set_json_mode(True)
        try:
            with self.assertRaises(SystemExit) as ctx:
                script.run(sys.executable, "-c", "import sys; sys.exit(2)")
            self.assertEqual(ctx.exception.code, 5)
        finally:
            log_mod.set_json_mode(False)


if __name__ == "__main__":
    unittest.main()
