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


class TestDockerFlags(unittest.TestCase):
    """Tests for Docker CLI flags."""

    def test_with_docker_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--with-docker", "build"])
        self.assertTrue(args.with_docker)
        self.assertFalse(args.with_minimal_docker)
        self.assertIsNone(args.docker_image)

    def test_with_minimal_docker_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--with-minimal-docker", "build"])
        self.assertFalse(args.with_docker)
        self.assertTrue(args.with_minimal_docker)
        self.assertIsNone(args.docker_image)

    def test_docker_image_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--docker-image", "my/image:tag", "build"])
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
            parser.parse_args(["--with-docker", "--with-minimal-docker", "build"])

    def test_with_docker_and_docker_image_mutually_exclusive(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--with-docker", "--docker-image", "img", "build"])

    def test_with_minimal_docker_and_docker_image_mutually_exclusive(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["--with-minimal-docker", "--docker-image", "img", "build"]
            )

    def test_docker_flags_before_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--with-docker", "test"])
        self.assertTrue(args.with_docker)
        self.assertEqual(args.subcommand, "test")


if __name__ == "__main__":
    unittest.main()
