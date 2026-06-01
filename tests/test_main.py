# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.__main__ (CLI entry point)."""

import sys
import tempfile
import textwrap
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from nanvix_zutil.__main__ import main
from nanvix_zutil.lockfile import get_zutil_version
from nanvix_zutil import paths


class TestVersionFlag(unittest.TestCase):
    """``nanvix-zutil --version`` prints version and exits 0."""

    def test_version_output(self) -> None:
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-zutil", "--version"]),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
        self.assertEqual(ctx.exception.code, 0)
        output = buf.getvalue().strip()
        self.assertIn(get_zutil_version(), output)


class TestHelpFlag(unittest.TestCase):
    """``nanvix-zutil --help`` and ``nanvix-zutil`` print help text."""

    def test_help_flag(self) -> None:
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-zutil", "--help"]),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
        self.assertEqual(ctx.exception.code, 0)
        output = buf.getvalue()
        self.assertIn("nanvix-zutil", output)
        self.assertIn("info", output)
        self.assertIn("resolve", output)

    def test_no_subcommand_prints_help(self) -> None:
        buf = StringIO()
        sys.stdout = buf
        try:
            with patch("sys.argv", ["nanvix-zutil"]):
                main()
        finally:
            sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("usage:", output)


class TestInfoDispatch(unittest.TestCase):
    """``nanvix-zutil info`` dispatches to info.main()."""

    @patch("nanvix_zutil.info.main")
    def test_info_dispatches(self, mock_info_main: MagicMock) -> None:
        """info subcommand calls info.main()."""
        original_argv = sys.argv[:]
        try:
            with patch("sys.argv", ["nanvix-zutil", "info", "--json"]):
                main()
        except SystemExit:
            pass
        finally:
            sys.argv = original_argv
        mock_info_main.assert_called_once()


class TestResolveDispatch(unittest.TestCase):
    """``nanvix-zutil resolve`` dispatches to resolve_cmd.main()."""

    @patch("nanvix_zutil.resolve_cmd.main")
    def test_resolve_dispatches(self, mock_resolve_main: MagicMock) -> None:
        original_argv = sys.argv[:]
        try:
            with patch("sys.argv", ["nanvix-zutil", "resolve"]):
                main()
        except SystemExit:
            pass
        finally:
            sys.argv = original_argv
        mock_resolve_main.assert_called_once()


class TestLintDispatch(unittest.TestCase):
    """``nanvix-zutil lint`` dispatches to lint_cmd.main() with rewritten argv."""

    def test_lint_dispatches(self) -> None:
        captured: dict[str, list[str]] = {}

        def fake_lint_main() -> None:
            captured["argv"] = sys.argv[:]

        original_argv = sys.argv[:]
        try:
            with (
                patch(
                    "nanvix_zutil.lint_cmd.main",
                    side_effect=fake_lint_main,
                ) as mock_lint_main,
                patch(
                    "sys.argv",
                    ["nanvix-zutil", "lint", "--nanvix-dir", "foo"],
                ),
            ):
                try:
                    main()
                except SystemExit:
                    pass
            mock_lint_main.assert_called_once()
        finally:
            sys.argv = original_argv
        self.assertEqual(
            captured["argv"],
            ["nanvix-zutil lint", "--nanvix-dir", "foo"],
        )


class TestConsumerCommandNoZPy(unittest.TestCase):
    """Consumer commands with no ``.nanvix/z.py`` exit with code 3."""

    def test_missing_z_py_exits_3(self) -> None:
        # Autouse fixture supplies an empty .nanvix/ under CWD; no z.py.
        with (
            patch("sys.argv", ["nanvix-zutil", "setup"]),
            self.assertRaises(SystemExit) as ctx,
        ):
            main()
        self.assertEqual(ctx.exception.code, 3)


class TestConsumerCommandWithZPy(unittest.TestCase):
    """Consumer commands with a valid ``.nanvix/z.py`` dispatch correctly."""

    def test_discover_and_dispatch(self) -> None:
        """Consumer subcommand discovers ZScript subclass and calls main()."""
        from nanvix_zutil.__main__ import (
            discover_script_class,
        )

        nanvix_dir = paths.nanvix_root()
        (paths.manifest_path()).write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n' 'nanvix-version = "0.1.0"\n'
        )
        (nanvix_dir / "z.py").write_text(textwrap.dedent("""\
            from nanvix_zutil.script import ZScript

            class TestScript(ZScript):
                def build(self):
                    pass

            if __name__ == "__main__":
                TestScript.main()
            """))

        cls = discover_script_class(nanvix_dir / "z.py")
        self.assertEqual(cls.__name__, "TestScript")

        # Verify main() dispatches correctly by mocking the
        # discovered class's main().
        with (
            patch("sys.argv", ["nanvix-zutil", "build"]),
            patch("nanvix_zutil.__main__.discover_script_class") as mock_discover,
        ):
            mock_cls = MagicMock()
            mock_discover.return_value = mock_cls
            main()
            mock_cls.main.assert_called_once_with()

    def test_discover_subclass(self) -> None:
        """discover_script_class finds the ZScript subclass in z.py."""
        from nanvix_zutil.__main__ import (
            discover_script_class,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            z_py = Path(tmpdir) / "z.py"
            z_py.write_text(textwrap.dedent("""\
                from nanvix_zutil.script import ZScript

                class MyBuild(ZScript):
                    def build(self):
                        pass

                if __name__ == "__main__":
                    MyBuild.main()
                """))
            cls = discover_script_class(z_py)
            self.assertIsNot(
                cls, __import__("nanvix_zutil.script", fromlist=["ZScript"]).ZScript
            )
            self.assertEqual(cls.__name__, "MyBuild")

    def test_no_subclass_exits(self) -> None:
        """discover_script_class exits with error if no subclass found."""
        from nanvix_zutil.__main__ import (
            discover_script_class,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            z_py = Path(tmpdir) / "z.py"
            z_py.write_text("x = 42\n")
            with self.assertRaises(SystemExit) as ctx:
                discover_script_class(z_py)
            self.assertEqual(ctx.exception.code, 1)

    def test_import_error_cleans_up_sys_modules(self) -> None:
        """discover_script_class cleans up sys.modules on import failure."""
        from nanvix_zutil.__main__ import discover_script_class

        with tempfile.TemporaryDirectory() as tmpdir:
            z_py = Path(tmpdir) / "z.py"
            z_py.write_text("raise RuntimeError('boom')\n")
            with self.assertRaises(SystemExit):
                discover_script_class(z_py)
            self.assertNotIn("_z_consumer", sys.modules)


class TestUnknownCommand(unittest.TestCase):
    """Unknown subcommand exits with code 2."""

    def test_unknown_command_exits_2(self) -> None:
        with (
            patch("sys.argv", ["nanvix-zutil", "nope"]),
            self.assertRaises(SystemExit) as ctx,
        ):
            main()
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
