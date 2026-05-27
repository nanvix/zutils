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
            if cmd == "install":
                argv += ["--output", "/tmp/out"]
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

    def test_distclean_not_registered(self) -> None:
        """distclean is standalone and must not be a consumer subcommand."""
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["distclean"])

    def test_available_param_restricts_subcommands(self) -> None:
        """build_parser(available=...) registers only the given subcommands."""
        available = ("setup", "build", "help")
        parser = build_parser(available=available)
        # Registered commands parse correctly.
        for cmd in ("setup", "build", "help"):
            argv = [cmd]
            if cmd == "setup":
                argv += ["--with-docker", "test/image:tag"]
            args = parser.parse_args(argv)
            self.assertEqual(args.subcommand, cmd)
        # Unregistered command raises SystemExit.
        with self.assertRaises(SystemExit):
            parser.parse_args(["test"])

    def test_available_param_rejects_distclean(self) -> None:
        """distclean is no longer a known subcommand for build_parser."""
        with self.assertRaises(ValueError):
            build_parser(available=("distclean", "help"))

    def test_available_none_registers_all(self) -> None:
        """available=None (default) registers every subcommand."""
        parser = build_parser(available=None)
        for cmd in SUBCOMMANDS:
            argv = [cmd]
            # setup requires --with-docker IMAGE
            if cmd == "setup":
                argv += ["--with-docker", "test/image:tag"]
            # install requires --output PATH
            if cmd == "install":
                argv += ["--output", "/tmp/out"]
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
    """Tests for the format subcommand (lint is standalone, see test_lint_cmd)."""

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

    def test_lint_not_in_subcommands(self) -> None:
        """lint has moved to a standalone command and is no longer a consumer subcommand."""
        self.assertNotIn("lint", SUBCOMMANDS)

    def test_format_in_subcommands(self) -> None:
        self.assertIn("format", SUBCOMMANDS)

    def test_lint_unknown_in_available(self) -> None:
        """lint is rejected when passed in the available set."""
        with self.assertRaises(ValueError):
            build_parser(available=("lint", "help"))

    def test_format_available_restricted(self) -> None:
        """format is accepted when in the available set."""
        parser = build_parser(available=("format", "help"))
        args = parser.parse_args(["format"])
        self.assertEqual(args.subcommand, "format")


class TestOfflineFlag(unittest.TestCase):
    """Tests for the --offline flag on the setup subcommand."""

    def test_offline_flag_parsed(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["setup", "--with-docker", "img:t", "--offline"])
        self.assertTrue(args.offline)

    def test_offline_default_false(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["setup", "--with-docker", "img:t"])
        self.assertFalse(args.offline)

    def test_offline_rejected_on_build(self) -> None:
        """--offline is only accepted on setup, not build."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["build", "--offline"])
        self.assertEqual(ctx.exception.code, 2)


class TestWithNanvixFlag(unittest.TestCase):
    """Tests for the --with-nanvix flag on the setup subcommand."""

    def test_with_nanvix_parsed(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["setup", "--with-docker", "img:t", "--with-nanvix", "/some/path"]
        )
        self.assertEqual(args.with_nanvix, "/some/path")

    def test_with_nanvix_default_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["setup", "--with-docker", "img:t"])
        self.assertIsNone(args.with_nanvix)

    def test_with_nanvix_rejected_on_build(self) -> None:
        """--with-nanvix is only accepted on setup, not build."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["build", "--with-nanvix", "/p"])
        self.assertEqual(ctx.exception.code, 2)


class TestSysrootPathFlag(unittest.TestCase):
    """Tests for the --sysroot-path flag on the setup subcommand."""

    def test_sysroot_path_parsed(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["setup", "--with-docker", "img:t", "--sysroot-path", "/my/sysroot"]
        )
        self.assertEqual(args.sysroot_path, "/my/sysroot")

    def test_sysroot_path_default_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["setup", "--with-docker", "img:t"])
        self.assertIsNone(args.sysroot_path)

    def test_sysroot_path_rejected_on_test(self) -> None:
        """--sysroot-path is only accepted on setup."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["test", "--sysroot-path", "/p"])
        self.assertEqual(ctx.exception.code, 2)


class TestInstallArtifactsSubcommand(unittest.TestCase):
    """Tests for the install subcommand."""

    def test_install_artifacts_in_subcommands(self) -> None:
        self.assertIn("install", SUBCOMMANDS)

    def test_install_artifacts_requires_output(self) -> None:
        """install without --output is rejected."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["install"])
        self.assertEqual(ctx.exception.code, 2)

    def test_install_artifacts_output_parsed(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["install", "--output", "/tmp/out"])
        self.assertEqual(args.output, "/tmp/out")
        self.assertEqual(args.subcommand, "install")

    def test_install_artifacts_available_restricted(self) -> None:
        """install is accepted when in the available set."""
        parser = build_parser(available=("install", "help"))
        args = parser.parse_args(["install", "--output", "/tmp/x"])
        self.assertEqual(args.subcommand, "install")


if __name__ == "__main__":
    unittest.main()
