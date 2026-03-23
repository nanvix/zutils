# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.requirements."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.requirements import load_requirements


class TestLoadRequirementsFileNotFound(unittest.TestCase):
    """load_requirements exits 3 when the file does not exist."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_missing_file_exits_3(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"

        with self.assertRaises(SystemExit) as ctx:
            load_requirements(path)

        self.assertEqual(ctx.exception.code, 3)


class TestLoadRequirementsBasic(unittest.TestCase):
    """load_requirements parses a simple requirements file."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_single_dependency(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@latest\nzlib\n")

        reqs = load_requirements(path)

        self.assertEqual(reqs.sysroot_tag, "latest")
        self.assertEqual(len(reqs.dependencies), 1)
        self.assertEqual(reqs.dependencies[0].name, "zlib")
        self.assertEqual(reqs.dependencies[0].repo, "nanvix/zlib")
        self.assertEqual(reqs.dependencies[0].tag, "latest")

    def test_explicit_tags(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@fa06b88\nzlib@b7a6a3c\n")

        reqs = load_requirements(path)

        self.assertEqual(reqs.sysroot_tag, "fa06b88")
        self.assertEqual(len(reqs.dependencies), 1)
        self.assertEqual(reqs.dependencies[0].name, "zlib")
        self.assertEqual(reqs.dependencies[0].tag, "b7a6a3c-nanvix-fa06b88")

    def test_sysroot_and_multiple_deps(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@latest\nzlib\nopenssl@abc1234\n")

        reqs = load_requirements(path)

        self.assertEqual(reqs.sysroot_tag, "latest")
        self.assertEqual(len(reqs.dependencies), 2)
        self.assertEqual(reqs.dependencies[0].name, "zlib")
        self.assertEqual(reqs.dependencies[0].tag, "latest")
        self.assertEqual(reqs.dependencies[1].name, "openssl")
        self.assertEqual(reqs.dependencies[1].repo, "nanvix/openssl")
        self.assertEqual(reqs.dependencies[1].tag, "abc1234-nanvix-latest")


class TestLoadRequirementsCommentsAndBlanks(unittest.TestCase):
    """load_requirements ignores comments and blank lines."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_comments_and_blanks_ignored(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text(
            "# Nanvix sysroot\n"
            "nanvix@latest\n"
            "\n"
            "# Build dependencies\n"
            "zlib  # compression library\n"
            "\n"
        )

        reqs = load_requirements(path)

        self.assertEqual(reqs.sysroot_tag, "latest")
        self.assertEqual(len(reqs.dependencies), 1)
        self.assertEqual(reqs.dependencies[0].name, "zlib")


class TestLoadRequirementsMandatoryNanvix(unittest.TestCase):
    """The 'nanvix' entry is mandatory."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_missing_nanvix_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("zlib\n")

        with self.assertRaises(SystemExit) as ctx:
            load_requirements(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_bare_nanvix_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix\nzlib\n")

        with self.assertRaises(SystemExit) as ctx:
            load_requirements(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_empty_file_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("")

        with self.assertRaises(SystemExit) as ctx:
            load_requirements(path)

        self.assertEqual(ctx.exception.code, 2)


class TestLoadRequirementsMalformedEntries(unittest.TestCase):
    """Malformed entries with empty name or tag are rejected."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_empty_name_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@latest\n@tag\n")

        with self.assertRaises(SystemExit) as ctx:
            load_requirements(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_empty_tag_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@latest\nzlib@\n")

        with self.assertRaises(SystemExit) as ctx:
            load_requirements(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_empty_name_and_tag_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@latest\n@\n")

        with self.assertRaises(SystemExit) as ctx:
            load_requirements(path)

        self.assertEqual(ctx.exception.code, 2)


class TestLoadRequirementsDuplicates(unittest.TestCase):
    """Duplicate names: last entry wins."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_last_wins(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@latest\nzlib@aaa\nzlib@bbb\n")

        reqs = load_requirements(path)

        self.assertEqual(len(reqs.dependencies), 1)
        self.assertEqual(reqs.dependencies[0].tag, "bbb-nanvix-latest")


class TestLoadRequirementsEnvOverride(unittest.TestCase):
    """Environment variables override file-specified tags."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_nanvix_version_overrides_sysroot(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@latest\nzlib\n")

        with patch.dict(os.environ, {"NANVIX_VERSION": "override_sha"}):
            reqs = load_requirements(path)

        self.assertEqual(reqs.sysroot_tag, "override_sha")
        self.assertEqual(reqs.dependencies[0].tag, "latest")

    def test_nanvix_version_dep_overrides_dep(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@latest\nzlib@file_sha\n")

        with patch.dict(os.environ, {"NANVIX_VERSION_ZLIB": "env_sha"}):
            reqs = load_requirements(path)

        self.assertEqual(reqs.sysroot_tag, "latest")
        self.assertEqual(reqs.dependencies[0].tag, "env_sha-nanvix-latest")

    def test_env_overrides_both(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@file_nanvix\nzlib@file_zlib\n")

        with patch.dict(
            os.environ,
            {"NANVIX_VERSION": "env_nanvix", "NANVIX_VERSION_ZLIB": "env_zlib"},
        ):
            reqs = load_requirements(path)

        self.assertEqual(reqs.sysroot_tag, "env_nanvix")
        self.assertEqual(reqs.dependencies[0].tag, "env_zlib-nanvix-env_nanvix")


class TestLoadRequirementsDefaultTag(unittest.TestCase):
    """When @tag is omitted, the tag defaults to 'latest'."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_no_at_defaults_to_latest(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@latest\nzlib\nopenssl\n")

        reqs = load_requirements(path)

        self.assertEqual(reqs.sysroot_tag, "latest")
        for dep in reqs.dependencies:
            self.assertEqual(dep.tag, "latest")


class TestLoadRequirementsAutoSuffix(unittest.TestCase):
    """Dependency tags are auto-suffixed with the nanvix version."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_short_tag_suffixed(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@fa06b88\nzlib@b7a6a3c\n")

        reqs = load_requirements(path)

        self.assertEqual(reqs.dependencies[0].tag, "b7a6a3c-nanvix-fa06b88")

    def test_full_tag_with_nanvix_rejected(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@fa06b88\nzlib@b7a6a3c-nanvix-fa06b88\n")

        log_mod.set_json_mode(True)
        with self.assertRaises(SystemExit) as ctx:
            load_requirements(path)
        log_mod.set_json_mode(False)

        self.assertEqual(ctx.exception.code, 2)

    def test_latest_dep_not_suffixed(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@fa06b88\nzlib\n")

        reqs = load_requirements(path)

        self.assertEqual(reqs.dependencies[0].tag, "latest")

    def test_multiple_deps_suffixed(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix-requirements.txt"
        path.write_text("nanvix@abc123\nzlib@aaa\nopenssl@bbb\n")

        reqs = load_requirements(path)

        self.assertEqual(reqs.dependencies[0].tag, "aaa-nanvix-abc123")
        self.assertEqual(reqs.dependencies[1].tag, "bbb-nanvix-abc123")


if __name__ == "__main__":
    unittest.main()
