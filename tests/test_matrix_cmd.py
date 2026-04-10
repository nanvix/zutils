# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.matrix_cmd."""

import json
import sys
import tempfile
import textwrap
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from nanvix_zutil.buildroot import Ref, RefKind
from nanvix_zutil.manifest import BuildMatrix, Manifest
from nanvix_zutil.matrix_cmd import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest() -> Manifest:
    """Return a minimal Manifest with a known BuildMatrix."""
    return Manifest(
        name="test",
        version="0.1.0",
        sysroot_ref=Ref(kind=RefKind.TAG, value="0.12.266"),
        builds=BuildMatrix(
            dimensions={
                "platforms": ["hyperlight", "microvm"],
                "modes": ["standalone", "single-process", "multi-process"],
                "memory": ["128mb"],
            },
            exclude=[{"platform": "hyperlight", "mode": "standalone"}],
        ),
    )


def _make_manifest_no_excludes() -> Manifest:
    """Return a Manifest with an empty exclude list."""
    return Manifest(
        name="test",
        version="0.1.0",
        sysroot_ref=Ref(kind=RefKind.TAG, value="0.12.266"),
        builds=BuildMatrix(
            dimensions={
                "platforms": ["hyperlight"],
                "modes": ["standalone"],
                "memory": ["128mb"],
            },
            exclude=[],
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMatrixJsonOutput(unittest.TestCase):
    """``nanvix-zutil matrix`` emits correct JSON to stdout."""

    @patch("nanvix_zutil.matrix_cmd.load_manifest")
    def test_json_output_structure(self, mock_load: MagicMock) -> None:
        """JSON output contains expected keys and values."""
        mock_load.return_value = _make_manifest()

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "nanvix.toml"
            manifest.write_text(
                '[package]\nname = "test"\nversion = "0.1.0"\n'
                'nanvix-version = "0.12.266"\n'
            )

            buf = StringIO()
            sys.stdout = buf
            try:
                with (
                    patch(
                        "sys.argv",
                        ["nanvix-zutil matrix", f"--manifest={manifest}"],
                    ),
                    self.assertRaises(SystemExit) as ctx,
                ):
                    main()
            finally:
                sys.stdout = sys.__stdout__

            self.assertEqual(ctx.exception.code, 0)
            obj = json.loads(buf.getvalue().strip())
            self.assertEqual(obj["platforms"], ["hyperlight", "microvm"])
            self.assertEqual(
                obj["modes"],
                ["standalone", "single-process", "multi-process"],
            )
            self.assertEqual(obj["memory"], ["128mb"])
            self.assertEqual(
                obj["exclude"],
                [{"platform": "hyperlight", "mode": "standalone"}],
            )

    @patch("nanvix_zutil.matrix_cmd.load_manifest")
    def test_no_excludes_omits_key(self, mock_load: MagicMock) -> None:
        """When exclude list is empty, 'exclude' key is absent."""
        mock_load.return_value = _make_manifest_no_excludes()

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "nanvix.toml"
            manifest.write_text(
                '[package]\nname = "test"\nversion = "0.1.0"\n'
                'nanvix-version = "0.12.266"\n'
            )

            buf = StringIO()
            sys.stdout = buf
            try:
                with (
                    patch(
                        "sys.argv",
                        ["nanvix-zutil matrix", f"--manifest={manifest}"],
                    ),
                    self.assertRaises(SystemExit) as ctx,
                ):
                    main()
            finally:
                sys.stdout = sys.__stdout__

            self.assertEqual(ctx.exception.code, 0)
            obj = json.loads(buf.getvalue().strip())
            self.assertNotIn("exclude", obj)

    @patch("nanvix_zutil.matrix_cmd.load_manifest")
    def test_output_is_valid_json(self, mock_load: MagicMock) -> None:
        """stdout is parseable by json.loads()."""
        mock_load.return_value = _make_manifest()

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "nanvix.toml"
            manifest.write_text(
                '[package]\nname = "test"\nversion = "0.1.0"\n'
                'nanvix-version = "0.12.266"\n'
            )

            buf = StringIO()
            sys.stdout = buf
            try:
                with (
                    patch(
                        "sys.argv",
                        ["nanvix-zutil matrix", f"--manifest={manifest}"],
                    ),
                    self.assertRaises(SystemExit),
                ):
                    main()
            finally:
                sys.stdout = sys.__stdout__

            # Should not raise
            json.loads(buf.getvalue())


class TestMatrixMissingManifest(unittest.TestCase):
    """Missing manifest exits with code 3."""

    def test_missing_manifest_exits_3(self) -> None:
        with (
            patch(
                "sys.argv",
                ["nanvix-zutil matrix", "--manifest=/nonexistent/nanvix.toml"],
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            main()
        self.assertEqual(ctx.exception.code, 3)


class TestMatrixWithRealToml(unittest.TestCase):
    """``nanvix-zutil matrix`` reads a real TOML manifest."""

    def test_reads_real_toml(self) -> None:
        """Write a nanvix.toml with [builds] and verify JSON output."""
        toml_content = textwrap.dedent("""\
            [package]
            name = "test-project"
            version = "1.0.0"
            nanvix-version = "0.12.266"

            [builds.matrix]
            platforms = ["hyperlight", "microvm"]
            modes = ["multi-process"]
            memory = ["256mb"]

            [[builds.exclude]]
            platform = "hyperlight"
            mode = "multi-process"
        """)

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "nanvix.toml"
            manifest.write_text(toml_content)

            buf = StringIO()
            sys.stdout = buf
            try:
                with (
                    patch(
                        "sys.argv",
                        ["nanvix-zutil matrix", f"--manifest={manifest}"],
                    ),
                    self.assertRaises(SystemExit) as ctx,
                ):
                    main()
            finally:
                sys.stdout = sys.__stdout__

            self.assertEqual(ctx.exception.code, 0)
            obj = json.loads(buf.getvalue().strip())
            self.assertEqual(obj["platforms"], ["hyperlight", "microvm"])
            self.assertEqual(obj["modes"], ["multi-process"])
            self.assertEqual(obj["memory"], ["256mb"])
            self.assertEqual(
                obj["exclude"],
                [{"platform": "hyperlight", "mode": "multi-process"}],
            )


class TestMatrixDispatchFromMain(unittest.TestCase):
    """``nanvix-zutil matrix`` dispatches to matrix_cmd.main()."""

    @patch("nanvix_zutil.matrix_cmd.main")
    def test_matrix_dispatches(self, mock_matrix_main: MagicMock) -> None:
        from nanvix_zutil.__main__ import main as cli_main

        original_argv = sys.argv[:]
        try:
            with patch("sys.argv", ["nanvix-zutil", "matrix"]):
                cli_main()
        except SystemExit:
            pass
        finally:
            sys.argv = original_argv
        mock_matrix_main.assert_called_once()


if __name__ == "__main__":
    unittest.main()
