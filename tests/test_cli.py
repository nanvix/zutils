# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.cli."""

import unittest

from nanvix_zutil.cli import SUBCOMMANDS, build_parser


class TestBuildParser(unittest.TestCase):
    """Tests for cli.build_parser."""

    def test_returns_parser(self) -> None:
        import argparse

        parser = build_parser()
        self.assertIsInstance(parser, argparse.ArgumentParser)

    def test_json_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--json", "build"])
        self.assertTrue(args.json)

    def test_no_json_by_default(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["build"])
        self.assertFalse(args.json)

    def test_subcommands_registered(self) -> None:
        parser = build_parser()
        for cmd in SUBCOMMANDS:
            args = parser.parse_args([cmd])
            self.assertEqual(args.subcommand, cmd)

    def test_no_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.subcommand)

    def test_version_exits(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_distclean_registered(self) -> None:
        """distclean must be in the default parser."""
        parser = build_parser()
        args = parser.parse_args(["distclean"])
        self.assertEqual(args.subcommand, "distclean")

    def test_available_param_restricts_subcommands(self) -> None:
        """build_parser(available=...) registers only the given subcommands."""
        available = ("setup", "distclean", "build", "help")
        parser = build_parser(available=available)
        # Registered commands parse correctly.
        for cmd in ("setup", "distclean", "build", "help"):
            args = parser.parse_args([cmd])
            self.assertEqual(args.subcommand, cmd)
        # Unregistered command raises SystemExit.
        with self.assertRaises(SystemExit):
            parser.parse_args(["test"])

    def test_available_none_registers_all(self) -> None:
        """available=None (default) registers every subcommand."""
        parser = build_parser(available=None)
        for cmd in SUBCOMMANDS:
            args = parser.parse_args([cmd])
            self.assertEqual(args.subcommand, cmd)


if __name__ == "__main__":
    unittest.main()
