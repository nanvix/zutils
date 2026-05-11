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
            argv = [cmd]
            if cmd == "setup":
                argv += ["--with-docker", "test/image:tag"]
            args = parser.parse_args(argv)
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
            argv = [cmd]
            if cmd == "setup":
                argv += ["--with-docker", "test/image:tag"]
            args = parser.parse_args(argv)
            self.assertEqual(args.subcommand, cmd)
        # Unregistered command raises SystemExit.
        with self.assertRaises(SystemExit):
            parser.parse_args(["test"])

    def test_available_none_registers_all(self) -> None:
        """available=None (default) registers every subcommand."""
        parser = build_parser(available=None)
        for cmd in SUBCOMMANDS:
            argv = [cmd]
            # setup requires --with-docker IMAGE
            if cmd == "setup":
                argv += ["--with-docker", "test/image:tag"]
            args = parser.parse_args(argv)
            self.assertEqual(args.subcommand, cmd)


class TestDockerFlags(unittest.TestCase):
    """Tests for Docker CLI flags (subcommand-level)."""

    def test_with_docker_requires_image(self) -> None:
        """--with-docker without an image argument is rejected."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["setup", "--with-docker"])
        self.assertEqual(ctx.exception.code, 2)

    def test_with_docker_custom_image(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["setup", "--with-docker", "my/image:tag"])
        self.assertEqual(args.with_docker, "my/image:tag")

    def test_setup_requires_with_docker(self) -> None:
        """setup without --with-docker is rejected."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["setup"])
        self.assertEqual(ctx.exception.code, 2)

    def test_docker_flags_rejected_on_build(self) -> None:
        """build subcommand does not accept Docker flags (moved to setup)."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["build", "--with-docker"])
        self.assertEqual(ctx.exception.code, 2)

    def test_docker_flags_rejected_on_release(self) -> None:
        """release subcommand does not accept Docker flags (moved to setup)."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["release", "--with-docker"])
        self.assertEqual(ctx.exception.code, 2)

    def test_docker_flags_rejected_on_test(self) -> None:
        """test subcommand does not accept Docker flags."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["test", "--with-docker"])
        self.assertEqual(ctx.exception.code, 2)

    def test_legacy_docker_before_subcommand_rejected(self) -> None:
        """Legacy ordering (--with-docker build) is rejected after migration."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--with-docker", "build"])
        self.assertEqual(ctx.exception.code, 2)


class TestLintFormatSubcommands(unittest.TestCase):
    """Tests for lint and format subcommands."""

    def test_lint_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["lint"])
        self.assertEqual(args.subcommand, "lint")

    def test_format_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["format"])
        self.assertEqual(args.subcommand, "format")

    def test_format_check_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["format", "--check"])
        self.assertTrue(args.check)

    def test_format_check_default_false(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["format"])
        self.assertFalse(args.check)

    def test_lint_in_subcommands(self) -> None:
        self.assertIn("lint", SUBCOMMANDS)

    def test_format_in_subcommands(self) -> None:
        self.assertIn("format", SUBCOMMANDS)

    def test_lint_available_restricted(self) -> None:
        """lint is accepted when in the available set."""
        parser = build_parser(available=("lint", "help"))
        args = parser.parse_args(["lint"])
        self.assertEqual(args.subcommand, "lint")

    def test_format_available_restricted(self) -> None:
        """format is accepted when in the available set."""
        parser = build_parser(available=("format", "help"))
        args = parser.parse_args(["format"])
        self.assertEqual(args.subcommand, "format")


if __name__ == "__main__":
    unittest.main()
