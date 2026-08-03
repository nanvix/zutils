# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.cli."""

import os
import tempfile
import unittest
from pathlib import Path

from nanvix_zutil.cli import SUBCOMMANDS, CONFIG_FLAG_KEYS, build_parser


class TestBuildParser(unittest.TestCase):
    """Tests for cli.build_parser."""

    def test_returns_parser(self) -> None:
        import argparse

        parser = build_parser()
        self.assertIsInstance(parser, argparse.ArgumentParser)

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

    def test_config_flags_parse_into_config_keys(self) -> None:
        """Config flags store into their NANVIX_* dest on each subcommand."""
        parser = build_parser(available=("build",))
        args = parser.parse_args(
            ["build", "--machine", "microvm", "--mode", "standalone"]
        )
        self.assertEqual(getattr(args, "NANVIX_MACHINE"), "microvm")
        self.assertEqual(getattr(args, "NANVIX_DEPLOYMENT_MODE"), "standalone")

    def test_config_flags_default_to_none(self) -> None:
        parser = build_parser(available=("build",))
        args = parser.parse_args(["build"])
        for key in CONFIG_FLAG_KEYS:
            self.assertIsNone(getattr(args, key))

    def test_config_flag_rejects_invalid_choice(self) -> None:
        parser = build_parser(available=("build",))
        with self.assertRaises(SystemExit):
            parser.parse_args(["build", "--machine", "bogus"])

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

    def test_setup_allows_manifest_default_image(self) -> None:
        """setup may derive its image from an SDK manifest."""
        parser = build_parser()
        args = parser.parse_args(["setup"])
        self.assertIsNone(args.with_docker)
        self.assertFalse(args.allow_local_docker_override)

    def test_explicit_local_override_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "setup",
                "--with-docker",
                "local/image:dev",
                "--allow-local-docker-override",
            ]
        )
        self.assertTrue(args.allow_local_docker_override)

    def test_docker_flags_rejected_on_build(self) -> None:
        """build subcommand does not accept Docker flags (moved to setup)."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["build", "--with-docker"])
        self.assertEqual(ctx.exception.code, 2)

    def test_release_subcommand_removed(self) -> None:
        """release is no longer a consumer subcommand (moved to standalone)."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["release"])
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
        with tempfile.TemporaryDirectory() as tmp:
            args = parser.parse_args(
                ["setup", "--with-docker", "img:t", "--with-nanvix", tmp]
            )
            self.assertEqual(args.with_nanvix, str(Path(tmp).resolve(strict=True)))

    def test_with_nanvix_canonicalised_to_absolute(self) -> None:
        """Relative paths are resolved to absolute by the argparse type."""
        parser = build_parser()
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                Path("sub").mkdir()
                args = parser.parse_args(
                    ["setup", "--with-docker", "img:t", "--with-nanvix", "sub"]
                )
            finally:
                os.chdir(cwd)
            self.assertTrue(Path(args.with_nanvix).is_absolute())
            self.assertEqual(
                Path(args.with_nanvix).name,
                "sub",
            )

    def test_with_nanvix_default_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["setup", "--with-docker", "img:t"])
        self.assertIsNone(args.with_nanvix)

    def test_with_nanvix_rejects_missing_path(self) -> None:
        """Non-existent paths are rejected at parse time."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(
                [
                    "setup",
                    "--with-docker",
                    "img:t",
                    "--with-nanvix",
                    "/definitely/does/not/exist/xyzzy",
                ]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_with_nanvix_rejects_file(self) -> None:
        """A path that exists but is a file (not a directory) is rejected."""
        parser = build_parser()
        with tempfile.NamedTemporaryFile() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                parser.parse_args(
                    [
                        "setup",
                        "--with-docker",
                        "img:t",
                        "--with-nanvix",
                        tmp.name,
                    ]
                )
            self.assertEqual(ctx.exception.code, 2)

    def test_with_nanvix_rejects_empty_string(self) -> None:
        """Empty path is rejected (would otherwise resolve to CWD)."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["setup", "--with-docker", "img:t", "--with-nanvix", ""])
        self.assertEqual(ctx.exception.code, 2)

    def test_with_nanvix_rejected_on_build(self) -> None:
        """--with-nanvix is only accepted on setup, not build."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["build", "--with-nanvix", "/p"])
        self.assertEqual(ctx.exception.code, 2)


class TestWithDepsFlag(unittest.TestCase):
    """Tests for the --with-deps flag on the setup subcommand."""

    def test_single_entry(self) -> None:
        parser = build_parser()
        with tempfile.NamedTemporaryFile() as tmp:
            args = parser.parse_args(
                ["setup", "--with-docker", "img:t", "--with-deps", f"foo={tmp.name}"]
            )
            self.assertEqual(
                args.with_deps, {"foo": str(Path(tmp.name).resolve(strict=True))}
            )

    def test_multiple_entries(self) -> None:
        parser = build_parser()
        with tempfile.NamedTemporaryFile() as a, tempfile.NamedTemporaryFile() as b:
            args = parser.parse_args(
                [
                    "setup",
                    "--with-docker",
                    "img:t",
                    "--with-deps",
                    f"foo={a.name},bar={b.name}",
                ]
            )
            self.assertEqual(
                args.with_deps,
                {
                    "foo": str(Path(a.name).resolve(strict=True)),
                    "bar": str(Path(b.name).resolve(strict=True)),
                },
            )

    def test_path_canonicalised(self) -> None:
        parser = build_parser()
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                Path("m.toml").write_text("")
                args = parser.parse_args(
                    ["setup", "--with-docker", "img:t", "--with-deps", "foo=m.toml"]
                )
            finally:
                os.chdir(cwd)
            self.assertTrue(Path(args.with_deps["foo"]).is_absolute())

    def test_default_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["setup", "--with-docker", "img:t"])
        self.assertIsNone(args.with_deps)

    def test_rejects_missing_equals(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["setup", "--with-docker", "img:t", "--with-deps", "foo"])
        self.assertEqual(ctx.exception.code, 2)

    def test_rejects_empty_name(self) -> None:
        parser = build_parser()
        with tempfile.NamedTemporaryFile() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                parser.parse_args(
                    [
                        "setup",
                        "--with-docker",
                        "img:t",
                        "--with-deps",
                        f"={tmp.name}",
                    ]
                )
            self.assertEqual(ctx.exception.code, 2)

    def test_rejects_empty_path(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(
                ["setup", "--with-docker", "img:t", "--with-deps", "foo="]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_rejects_missing_path_dir(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(
                [
                    "setup",
                    "--with-docker",
                    "img:t",
                    "--with-deps",
                    "foo=/nope/xyzzy/def/not.toml",
                ]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_rejects_directory(self) -> None:
        """A directory path is rejected (must be a file)."""
        parser = build_parser()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                parser.parse_args(
                    ["setup", "--with-docker", "img:t", "--with-deps", f"foo={tmp}"]
                )
            self.assertEqual(ctx.exception.code, 2)

    def test_rejected_on_build(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["build", "--with-deps", "foo=/p"])
        self.assertEqual(ctx.exception.code, 2)

    def test_skips_empty_entries(self) -> None:
        """Empty entries (trailing/adjacent commas) are ignored."""
        parser = build_parser()
        with tempfile.NamedTemporaryFile() as tmp:
            args = parser.parse_args(
                [
                    "setup",
                    "--with-docker",
                    "img:t",
                    "--with-deps",
                    f",foo={tmp.name},,bar={tmp.name},",
                ]
            )
            self.assertEqual(set(args.with_deps), {"foo", "bar"})


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
