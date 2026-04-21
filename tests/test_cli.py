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


class TestDockerFlags(unittest.TestCase):
    """Tests for Docker CLI flags (subcommand-level)."""

    def test_with_docker_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["build", "--with-docker"])
        self.assertTrue(args.with_docker)
        self.assertFalse(args.with_minimal_docker)
        self.assertIsNone(args.docker_image)

    def test_with_minimal_docker_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["build", "--with-minimal-docker"])
        self.assertFalse(args.with_docker)
        self.assertTrue(args.with_minimal_docker)
        self.assertIsNone(args.docker_image)

    def test_docker_image_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["build", "--docker-image", "my/image:tag"])
        self.assertFalse(args.with_docker)
        self.assertFalse(args.with_minimal_docker)
        self.assertEqual(args.docker_image, "my/image:tag")

    def test_docker_flags_default_to_off(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["build"])
        self.assertFalse(args.with_docker)
        self.assertFalse(args.with_minimal_docker)
        self.assertIsNone(args.docker_image)

    def test_with_docker_and_with_minimal_docker_mutually_exclusive(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["build", "--with-docker", "--with-minimal-docker"])

    def test_with_docker_and_docker_image_mutually_exclusive(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["build", "--with-docker", "--docker-image", "img"])

    def test_with_minimal_docker_and_docker_image_mutually_exclusive(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["build", "--with-minimal-docker", "--docker-image", "img"]
            )

    def test_docker_flags_on_release_subcommand(self) -> None:
        """Docker flags are accepted on the release subcommand."""
        parser = build_parser()
        args = parser.parse_args(["release", "--with-docker"])
        self.assertTrue(args.with_docker)
        self.assertEqual(args.subcommand, "release")

    def test_docker_flags_rejected_on_test(self) -> None:
        """test subcommand does not accept Docker flags."""
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["test", "--with-docker"])

    def test_docker_flags_rejected_on_setup(self) -> None:
        """setup subcommand does not accept Docker flags."""
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["setup", "--with-docker"])


class TestAllBuildsFlags(unittest.TestCase):
    """Tests for --all-builds and --mode CLI flags."""

    def test_all_builds_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--all-builds", "build"])
        self.assertTrue(args.all_builds)

    def test_all_builds_default_false(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["build"])
        self.assertFalse(args.all_builds)

    def test_mode_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--mode", "multi-process", "build"])
        self.assertEqual(args.mode, "multi-process")

    def test_mode_default_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["build"])
        self.assertIsNone(args.mode)

    def test_all_builds_with_mode(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--all-builds", "--mode", "standalone", "test"])
        self.assertTrue(args.all_builds)
        self.assertEqual(args.mode, "standalone")
        self.assertEqual(args.subcommand, "test")

    def test_all_builds_with_docker(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--all-builds", "build", "--with-docker"])
        self.assertTrue(args.all_builds)
        self.assertTrue(args.with_docker)
        self.assertEqual(args.subcommand, "build")


if __name__ == "__main__":
    unittest.main()
