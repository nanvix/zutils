# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.format_cmd."""

import subprocess as sp
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP
from nanvix_zutil.format_cmd import format, main


def _patch_prefix(nanvix_dir: Path):
    """Patch ``sys.prefix`` so ``format()`` resolves *nanvix_dir*.

    ``format()`` computes ``nanvix_dir = Path(sys.prefix).parent`` on the
    assumption that the active venv lives at ``<nanvix-dir>/venv``.  Tests
    fake this by pointing ``sys.prefix`` at ``<nanvix-dir>/venv`` (the
    path need not exist on disk).
    """
    return patch("nanvix_zutil.format_cmd.sys.prefix", str(nanvix_dir / "venv"))


class TestFormatCmd(unittest.TestCase):
    """Behaviour of the standalone ``nanvix-zutil format`` command."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        self.nanvix_dir.mkdir()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_runs_black(self) -> None:
        """``format`` invokes black (without --check) on .nanvix/*.py."""
        (self.nanvix_dir / "z.py").write_text("x = 1\n")

        calls: list[list[str]] = []

        def fake_run(
            args: tuple[str, ...], **kwargs: object
        ) -> sp.CompletedProcess[str]:
            cmd = list(args)
            calls.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0)

        with (
            _patch_prefix(self.nanvix_dir),
            patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run),
            patch("importlib.util.find_spec", return_value=True),
        ):
            format()

        self.assertEqual(len(calls), 1)
        self.assertIn("-m", calls[0])
        self.assertIn("black", calls[0])
        self.assertIn("--config", calls[0])
        self.assertNotIn("--check", calls[0])

    def test_check_mode(self) -> None:
        """``format(check=True)`` runs black --check."""
        (self.nanvix_dir / "z.py").write_text("x = 1\n")

        calls: list[list[str]] = []

        def fake_run(
            args: tuple[str, ...], **kwargs: object
        ) -> sp.CompletedProcess[str]:
            cmd = list(args)
            calls.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0)

        with (
            _patch_prefix(self.nanvix_dir),
            patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run),
            patch("importlib.util.find_spec", return_value=True),
        ):
            format(check=True)

        self.assertEqual(len(calls), 1)
        self.assertIn("-m", calls[0])
        self.assertIn("black", calls[0])
        self.assertIn("--config", calls[0])
        self.assertIn("--check", calls[0])

    def test_no_py_files_warns(self) -> None:
        """Warns and returns when no .py files exist."""
        with (
            _patch_prefix(self.nanvix_dir),
            patch("nanvix_zutil.format_cmd.log") as mock_log,
        ):
            format()

        mock_log.warning.assert_called_once()
        self.assertIn("nothing to format", mock_log.warning.call_args[0][0].lower())

    def test_exits_on_failure(self) -> None:
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
                _patch_prefix(self.nanvix_dir),
                patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run),
                patch("importlib.util.find_spec", return_value=True),
                self.assertRaises(SystemExit) as ctx,
            ):
                format()
            self.assertEqual(ctx.exception.code, 5)
        finally:
            log_mod.set_json_mode(False)

    def test_exits_when_tool_missing(self) -> None:
        """Exits with EXIT_MISSING_DEP when black is not installed."""
        (self.nanvix_dir / "z.py").write_text("x = 1\n")

        log_mod.set_json_mode(True)
        try:
            with (
                _patch_prefix(self.nanvix_dir),
                patch("importlib.util.find_spec", return_value=None),
                self.assertRaises(SystemExit) as ctx,
            ):
                format()
            self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)
        finally:
            log_mod.set_json_mode(False)


class TestFormatCmdMain(unittest.TestCase):
    """``main()`` parses args and exits with EXIT_SUCCESS."""

    def test_main_no_py_files_exits_zero(self) -> None:
        """``main()`` exits 0 when the resolved nanvix dir has no .py files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nanvix_dir = Path(tmpdir) / ".nanvix"
            nanvix_dir.mkdir()
            with (
                _patch_prefix(nanvix_dir),
                patch("sys.argv", ["nanvix-zutil format"]),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
            self.assertEqual(ctx.exception.code, 0)

    def test_main_rejects_unknown_args(self) -> None:
        """The parser no longer accepts ``--nanvix-dir`` (resolved via sys.prefix)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nanvix_dir = Path(tmpdir) / "custom-dir"
            nanvix_dir.mkdir()
            with (
                _patch_prefix(nanvix_dir),
                patch(
                    "sys.argv",
                    ["nanvix-zutil format", "--nanvix-dir", str(nanvix_dir)],
                ),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
            # argparse exits 2 on unrecognised args
            self.assertEqual(ctx.exception.code, 2)

    def test_main_check_flag(self) -> None:
        """``--check`` is propagated to format()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nanvix_dir = Path(tmpdir) / ".nanvix"
            nanvix_dir.mkdir()
            (nanvix_dir / "z.py").write_text("x = 1\n")

            calls: list[list[str]] = []

            def fake_run(
                args: tuple[str, ...], **kwargs: object
            ) -> sp.CompletedProcess[str]:
                cmd = list(args)
                calls.append(cmd)
                return sp.CompletedProcess(args=cmd, returncode=0)

            with (
                _patch_prefix(nanvix_dir),
                patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run),
                patch("importlib.util.find_spec", return_value=True),
                patch("sys.argv", ["nanvix-zutil format", "--check"]),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
            self.assertEqual(ctx.exception.code, 0)
            self.assertIn("--check", calls[0])


if __name__ == "__main__":
    unittest.main()
