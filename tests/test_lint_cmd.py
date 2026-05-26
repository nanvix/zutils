# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.lint_cmd."""

import os
import subprocess as sp
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.exitcodes import EXIT_INVALID_ARGS, EXIT_MISSING_DEP
from nanvix_zutil.lint_cmd import lint, main


class TestLintCmd(unittest.TestCase):
    """Behaviour of the standalone ``nanvix-zutil lint`` command."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        self.nanvix_dir.mkdir()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_runs_black_and_pyright(self) -> None:
        """``lint`` invokes black --check then pyright on .nanvix/*.py."""
        (self.nanvix_dir / "z.py").write_text("x = 1\n")

        calls: list[list[str]] = []

        def fake_run(
            args: tuple[str, ...], **kwargs: object
        ) -> sp.CompletedProcess[str]:
            cmd = list(args)
            calls.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0)

        with (
            patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run),
            patch("importlib.util.find_spec", return_value=True),
        ):
            lint(self.nanvix_dir)

        self.assertEqual(len(calls), 2)
        self.assertIn("-m", calls[0])
        self.assertIn("black", calls[0])
        self.assertIn("--config", calls[0])
        self.assertIn("--check", calls[0])
        self.assertIn("-m", calls[1])
        self.assertIn("pyright", calls[1])
        self.assertIn("--project", calls[1])

    def test_no_py_files_warns(self) -> None:
        """Warns and returns when no .py files exist."""
        with patch("nanvix_zutil.lint_cmd.log") as mock_log:
            lint(self.nanvix_dir)

        mock_log.warning.assert_called_once()
        self.assertIn("nothing to lint", mock_log.warning.call_args[0][0].lower())

    def test_exits_on_black_failure(self) -> None:
        """Exits with EXIT_BUILD_FAILURE when black fails."""
        (self.nanvix_dir / "z.py").write_text("x = 1\n")

        def fake_run(
            args: tuple[str, ...], **kwargs: object
        ) -> sp.CompletedProcess[str]:
            cmd = list(args)
            raise sp.CalledProcessError(returncode=1, cmd=cmd)

        log_mod.set_json_mode(True)
        try:
            with (
                patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run),
                patch("importlib.util.find_spec", return_value=True),
                self.assertRaises(SystemExit) as ctx,
            ):
                lint(self.nanvix_dir)
            self.assertEqual(ctx.exception.code, 5)
        finally:
            log_mod.set_json_mode(False)

    def test_exits_when_tool_missing(self) -> None:
        """Exits with EXIT_MISSING_DEP when black is not installed."""
        (self.nanvix_dir / "z.py").write_text("x = 1\n")

        log_mod.set_json_mode(True)
        try:
            with (
                patch("importlib.util.find_spec", return_value=None),
                self.assertRaises(SystemExit) as ctx,
            ):
                lint(self.nanvix_dir)
            self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)
        finally:
            log_mod.set_json_mode(False)

    def test_exits_when_nanvix_dir_not_a_directory(self) -> None:
        """Exits with EXIT_INVALID_ARGS when --nanvix-dir is not a directory."""
        bogus = Path(self._tmpdir.name) / "not-a-dir"
        bogus.write_text("")  # regular file, not a directory

        log_mod.set_json_mode(True)
        try:
            with self.assertRaises(SystemExit) as ctx:
                lint(bogus)
            self.assertEqual(ctx.exception.code, EXIT_INVALID_ARGS)
        finally:
            log_mod.set_json_mode(False)

    def test_exits_when_nanvix_dir_missing(self) -> None:
        """Exits with EXIT_INVALID_ARGS when --nanvix-dir does not exist."""
        missing = Path(self._tmpdir.name) / "missing"

        log_mod.set_json_mode(True)
        try:
            with self.assertRaises(SystemExit) as ctx:
                lint(missing)
            self.assertEqual(ctx.exception.code, EXIT_INVALID_ARGS)
        finally:
            log_mod.set_json_mode(False)


class TestLintCmdMain(unittest.TestCase):
    """``main()`` wires --nanvix-dir and exits with EXIT_SUCCESS."""

    def test_main_default_nanvix_dir(self) -> None:
        """``main()`` defaults to ``.nanvix`` and exits 0 when no .py files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".nanvix").mkdir()
            cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                with (
                    patch("sys.argv", ["nanvix-zutil lint"]),
                    self.assertRaises(SystemExit) as ctx,
                ):
                    main()
                self.assertEqual(ctx.exception.code, 0)
            finally:
                os.chdir(cwd)

    def test_main_explicit_nanvix_dir(self) -> None:
        """``--nanvix-dir`` is honoured."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nanvix_dir = Path(tmpdir) / "custom-dir"
            nanvix_dir.mkdir()
            with (
                patch(
                    "sys.argv", ["nanvix-zutil lint", "--nanvix-dir", str(nanvix_dir)]
                ),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
            self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
