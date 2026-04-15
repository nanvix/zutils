# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.manifest."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.buildroot import RefKind
from tests.testutils import MANIFEST_WITH_BUILDS
from nanvix_zutil.manifest import (
    _is_local_path,  # pyright: ignore[reportPrivateUsage]
    load_manifest,
)


class TestLoadManifestFileNotFound(unittest.TestCase):
    """load_manifest exits 3 when the file does not exist."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_missing_file_exits_3(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 3)


class TestLoadManifestBasic(unittest.TestCase):
    """load_manifest parses a well-formed nanvix.toml."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_single_dependency_commitish(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = { commitish = "b7a6a3c" }\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.name, "myapp")
        self.assertEqual(m.version, "1.0.0")
        self.assertEqual(m.sysroot_ref.kind, RefKind.TAG)
        self.assertEqual(m.sysroot_ref.value, "0.12.257")
        self.assertEqual(len(m.dependencies), 1)
        self.assertEqual(m.dependencies[0].name, "zlib")
        self.assertEqual(m.dependencies[0].repo, "nanvix/zlib")
        self.assertEqual(m.dependencies[0].ref.kind, RefKind.COMMITISH)
        self.assertEqual(m.dependencies[0].ref.value, "b7a6a3c")

    def test_single_dependency_version_string(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "1.2.3"\n'
            "[dependencies]\n"
            'zlib = "4.5.6"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.sysroot_ref.kind, RefKind.TAG)
        self.assertEqual(m.sysroot_ref.value, "1.2.3")
        self.assertEqual(m.dependencies[0].ref.kind, RefKind.VERSION)
        self.assertEqual(m.dependencies[0].ref.value, "4.5.6-nanvix-1.2.3")

    def test_single_dependency_version_table(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "1.2.3"\n'
            "[dependencies]\n"
            'zlib = { version = "4.5.6" }\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.VERSION)
        self.assertEqual(m.dependencies[0].ref.value, "4.5.6-nanvix-1.2.3")

    def test_single_dependency_tag(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = { tag = "675d8f2-nanvix-e63706b" }\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.TAG)
        self.assertEqual(m.dependencies[0].ref.value, "675d8f2-nanvix-e63706b")

    def test_single_dependency_id(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            "zlib = { id = 12345678 }\n"
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.ID)
        self.assertEqual(m.dependencies[0].ref.value, 12345678)

    def test_mixed_dependencies(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "1.0.0"\n'
            "[dependencies]\n"
            'zlib = { commitish = "aaa" }\n'
            'openssl = "2.0.0"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.sysroot_ref.value, "1.0.0")
        self.assertEqual(len(m.dependencies), 2)
        self.assertEqual(m.dependencies[0].ref.kind, RefKind.COMMITISH)
        self.assertEqual(m.dependencies[0].ref.value, "aaa")
        self.assertEqual(m.dependencies[1].ref.kind, RefKind.VERSION)
        self.assertEqual(m.dependencies[1].ref.value, "2.0.0-nanvix-1.0.0")

    def test_no_dependencies_section(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(len(m.dependencies), 0)
        self.assertEqual(len(m.system_dependencies), 0)

    def test_empty_dependencies_section(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(len(m.dependencies), 0)

    def test_empty_system_dependencies_section(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[system-dependencies]\n"
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(len(m.system_dependencies), 0)


class TestLoadManifestInvalidVersion(unittest.TestCase):
    """Invalid version specs are rejected."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_empty_string_nanvix_version_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            '[package]\nname = "myapp"\nversion = "1.0.0"\nnanvix-version = ""\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_whitespace_nanvix_version_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "has space"\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_non_semver_nanvix_version_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            '[package]\nname = "myapp"\nversion = "1.0.0"\nnanvix-version = "abc123"\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_latest_nanvix_version_accepted(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "latest"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.sysroot_ref.kind, RefKind.TAG)
        self.assertEqual(m.sysroot_ref.value, "latest")

    def test_latest_sysroot_skips_dep_suffix(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "latest"\n'
            "[dependencies]\n"
            'zlib = "1.3.1"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.sysroot_ref.value, "latest")
        # Dep should NOT be suffixed — deferred to resolver
        self.assertEqual(m.dependencies[0].ref.value, "1.3.1")

    def test_latest_sysroot_env_override_suffixes_normally(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "latest"\n'
            "[dependencies]\n"
            'zlib = "1.3.1"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        with patch.dict("os.environ", {"NANVIX_VERSION": "0.12.258"}):
            m = load_manifest(path)

        self.assertEqual(m.sysroot_ref.value, "0.12.258")
        self.assertEqual(m.dependencies[0].ref.value, "1.3.1-nanvix-0.12.258")

    def test_nanvix_version_table_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = { tag = "v1.0.0" }\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_whitespace_dep_version_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = "has space"\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_empty_commitish_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = { commitish = "" }\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_non_string_commitish_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            "zlib = { commitish = 123 }\n"
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_multiple_specifier_keys_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = { version = "1.0", tag = "v1.0" }\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_non_integer_id_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = { id = "abc" }\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_unknown_table_key_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = { foo = "bar" }\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)


class TestLoadManifestLatestWarning(unittest.TestCase):
    """'latest' emits a warning but is accepted for dependencies."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_latest_dep_version_warns(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = "latest"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        with patch("nanvix_zutil.manifest.log") as mock_log:
            m = load_manifest(path)

        mock_log.warning.assert_called()
        self.assertEqual(m.dependencies[0].ref.value, "latest-nanvix-0.12.257")


class TestLoadManifestPackageValidation(unittest.TestCase):
    """Required [package] keys are validated."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_missing_package_section_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text("")

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_missing_name_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text('[package]\nversion = "1.0.0"\nnanvix-version = "0.12.257"\n')

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_missing_version_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text('[package]\nname = "myapp"\nnanvix-version = "0.12.257"\n')

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_missing_nanvix_version_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text('[package]\nname = "myapp"\nversion = "1.0.0"\n')

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_empty_file_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text("")

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)


class TestLoadManifestMalformedToml(unittest.TestCase):
    """Invalid TOML syntax is rejected."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_invalid_toml_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text("this is not valid toml [[[")

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)


class TestLoadManifestEnvOverride(unittest.TestCase):
    """Environment variables override manifest versions."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_nanvix_version_overrides_sysroot(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = "1.0.0"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        with patch.dict(os.environ, {"NANVIX_VERSION": "override_sha"}):
            m = load_manifest(path)

        self.assertEqual(m.sysroot_ref.value, "override_sha")
        self.assertEqual(m.dependencies[0].ref.value, "1.0.0-nanvix-override_sha")

    def test_nanvix_version_v_prefix_stripped_in_suffix(self) -> None:
        """NANVIX_VERSION with 'v' prefix must strip it for dep suffix."""
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "latest"\n'
            "[dependencies]\n"
            'zlib = "1.3.1"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        with patch.dict(os.environ, {"NANVIX_VERSION": "v0.12.291"}):
            m = load_manifest(path)

        self.assertEqual(m.sysroot_ref.value, "v0.12.291")
        self.assertEqual(m.dependencies[0].ref.value, "1.3.1-nanvix-0.12.291")

    def test_nanvix_version_dep_overrides_dep(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = "1.0.0"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        with patch.dict(os.environ, {"NANVIX_VERSION_ZLIB": "env_sha"}):
            m = load_manifest(path)

        self.assertEqual(m.sysroot_ref.value, "0.12.257")
        self.assertEqual(m.dependencies[0].ref.value, "env_sha-nanvix-0.12.257")

    def test_env_overrides_both(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = "1.0.0"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        with patch.dict(
            os.environ,
            {"NANVIX_VERSION": "env_nanvix", "NANVIX_VERSION_ZLIB": "env_zlib"},
        ):
            m = load_manifest(path)

        self.assertEqual(m.sysroot_ref.value, "env_nanvix")
        self.assertEqual(m.dependencies[0].ref.value, "env_zlib-nanvix-env_nanvix")

    def test_env_override_with_nanvix_suffix_skips_auto_suffix(self) -> None:
        """NANVIX_VERSION_<NAME> with -nanvix- suffix bypasses auto-suffixing."""
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.410"\n'
            "[dependencies]\n"
            'zlib = "1.3.1"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        with patch.dict(os.environ, {"NANVIX_VERSION_ZLIB": "1.3.1-nanvix-99.99.99"}):
            m = load_manifest(path)

        # The env override already has the suffix — must NOT be double-suffixed.
        self.assertEqual(m.dependencies[0].ref.value, "1.3.1-nanvix-99.99.99")

    def test_env_override_without_suffix_still_gets_suffixed(self) -> None:
        """NANVIX_VERSION_<NAME> without -nanvix- still gets auto-suffixed."""
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.410"\n'
            "[dependencies]\n"
            'zlib = "1.3.1"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        with patch.dict(os.environ, {"NANVIX_VERSION_ZLIB": "2.0.0"}):
            m = load_manifest(path)

        # Plain base version override still gets the nanvix suffix.
        self.assertEqual(m.dependencies[0].ref.value, "2.0.0-nanvix-0.12.410")


class TestLoadManifestAutoSuffix(unittest.TestCase):
    """Only VERSION refs are auto-suffixed with the nanvix version."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_version_string_dep_suffixed(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "1.0.0"\n'
            "[dependencies]\n"
            'zlib = "4.5.6"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.VERSION)
        self.assertEqual(m.dependencies[0].ref.value, "4.5.6-nanvix-1.0.0")

    def test_version_table_dep_suffixed(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "1.0.0"\n'
            "[dependencies]\n"
            'zlib = { version = "4.5.6" }\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.VERSION)
        self.assertEqual(m.dependencies[0].ref.value, "4.5.6-nanvix-1.0.0")

    def test_commitish_dep_not_suffixed(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = { commitish = "b7a6a3c" }\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.COMMITISH)
        self.assertEqual(m.dependencies[0].ref.value, "b7a6a3c")

    def test_tag_dep_not_suffixed(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = { tag = "v1.0.0-nanvix-abc" }\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.TAG)
        self.assertEqual(m.dependencies[0].ref.value, "v1.0.0-nanvix-abc")

    def test_id_dep_not_suffixed(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            "zlib = { id = 99999 }\n"
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.ID)
        self.assertEqual(m.dependencies[0].ref.value, 99999)

    def test_full_ref_with_nanvix_rejected(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = "b7a6a3c-nanvix-fa06b88"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        log_mod.set_json_mode(True)
        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)
        log_mod.set_json_mode(False)

        self.assertEqual(ctx.exception.code, 2)

    def test_multiple_version_deps_suffixed(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "1.0.0"\n'
            "[dependencies]\n"
            'zlib = "1.2.3"\n'
            'openssl = "2.0.0"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.dependencies[0].ref.value, "1.2.3-nanvix-1.0.0")
        self.assertEqual(m.dependencies[1].ref.value, "2.0.0-nanvix-1.0.0")

    def test_system_deps_version_suffixed(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[system-dependencies]\n"
            'foobar = "1.2.3"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(m.system_dependencies[0].name, "foobar")
        self.assertEqual(m.system_dependencies[0].ref.kind, RefKind.VERSION)
        self.assertEqual(m.system_dependencies[0].ref.value, "1.2.3-nanvix-0.12.257")


class TestLoadManifestSystemDependencies(unittest.TestCase):
    """[system-dependencies] are parsed correctly."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_system_deps_parsed(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[system-dependencies]\n"
            'foobar = "1.2.3"\n'
            'bazqux = { commitish = "deadbeef" }\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(len(m.system_dependencies), 2)
        self.assertEqual(m.system_dependencies[0].name, "foobar")
        self.assertEqual(m.system_dependencies[0].repo, "nanvix/foobar")
        self.assertEqual(m.system_dependencies[0].ref.kind, RefKind.VERSION)
        self.assertEqual(m.system_dependencies[0].ref.value, "1.2.3-nanvix-0.12.257")
        self.assertEqual(m.system_dependencies[1].name, "bazqux")
        self.assertEqual(m.system_dependencies[1].ref.kind, RefKind.COMMITISH)
        self.assertEqual(m.system_dependencies[1].ref.value, "deadbeef")

    def test_both_dep_sections_parsed(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = { commitish = "aaa" }\n'
            "[system-dependencies]\n"
            'foobar = "1.2.3"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        m = load_manifest(path)

        self.assertEqual(len(m.dependencies), 1)
        self.assertEqual(len(m.system_dependencies), 1)


class TestLoadManifestTypeValidation(unittest.TestCase):
    """Non-string/non-table dependency values and non-table sections are rejected."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_integer_dependency_value_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            "zlib = 123\n"
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_boolean_dependency_value_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            "zlib = true\n"
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_dependencies_not_table_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            'dependencies = "not-a-table"\n'
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_system_dependencies_not_table_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "system-dependencies = 42\n"
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)


<<<<<<< HEAD
class TestLoadManifestBuildsSection(unittest.TestCase):
    """Tests for the required [builds] section in nanvix.toml."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_builds_section_parsed(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(MANIFEST_WITH_BUILDS)

        m = load_manifest(path)

        self.assertEqual(m.builds.dimensions["platforms"], ["hyperlight", "microvm"])
        self.assertEqual(m.builds.dimensions["modes"], ["multi-process", "standalone"])
        self.assertEqual(m.builds.dimensions["memory"], ["128mb"])
        self.assertEqual(len(m.builds.exclude), 1)
        self.assertEqual(m.builds.exclude[0]["platform"], "hyperlight")
        self.assertEqual(m.builds.exclude[0]["mode"], "standalone")

    def test_no_builds_section_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_builds_matrix_required(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "\n"
            "[builds]\n"
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_builds_invalid_dimension_type_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "\n"
            "[builds.matrix]\n"
            'platforms = "not-a-list"\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_builds_exclude_references_unknown_key_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
            "\n"
            "[[builds.exclude]]\n"
            'unknown_dim = "value"\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_builds_empty_dimension_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "\n"
            "[builds.matrix]\n"
            "platforms = []\n"
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_builds_missing_required_dimension_exits_2(self) -> None:
        """Missing 'modes' dimension in [builds.matrix] causes exit."""
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'memory = ["128mb"]\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_builds_empty_exclude_entry_exits_2(self) -> None:
        """An empty [[builds.exclude]] table causes exit."""
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
            "\n"
            "[[builds.exclude]]\n"
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)
||||||| parent of f129376 ([tests] F: Add tests for RefKind.LOCAL)
=======
class TestIsLocalPath(unittest.TestCase):
    """_is_local_path() detects filesystem paths correctly."""

    def test_unix_absolute(self) -> None:
        self.assertTrue(_is_local_path("/home/me/zlib-build"))

    def test_unix_root(self) -> None:
        self.assertTrue(_is_local_path("/"))

    def test_relative_dot_slash(self) -> None:
        self.assertTrue(_is_local_path("./local-build"))

    def test_relative_dot_dot_slash(self) -> None:
        self.assertTrue(_is_local_path("../sibling/build"))

    def test_tilde_home(self) -> None:
        self.assertTrue(_is_local_path("~/nanvix"))

    def test_windows_drive_backslash(self) -> None:
        self.assertTrue(_is_local_path("C:\\Users\\me\\nanvix"))

    def test_windows_drive_forward_slash(self) -> None:
        self.assertTrue(_is_local_path("D:/builds/nanvix"))

    def test_windows_unc(self) -> None:
        self.assertTrue(_is_local_path("\\\\server\\share"))

    def test_semver_string(self) -> None:
        self.assertFalse(_is_local_path("1.3.1"))

    def test_latest(self) -> None:
        self.assertFalse(_is_local_path("latest"))

    def test_v_prefixed_version(self) -> None:
        self.assertFalse(_is_local_path("v0.12.291"))

    def test_commit_hash(self) -> None:
        self.assertFalse(_is_local_path("b7a6a3c"))

    def test_tag_string(self) -> None:
        self.assertFalse(_is_local_path("v1.0.0-nanvix-abc"))


class TestLoadManifestLocalOverride(unittest.TestCase):
    """Env var overrides with filesystem paths produce RefKind.LOCAL."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_dep_override_unix_path(self) -> None:
        """NANVIX_VERSION_ZLIB=/path/to/zlib -> RefKind.LOCAL."""
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = "1.0.0"\n'
        )

        with patch.dict(os.environ, {"NANVIX_VERSION_ZLIB": "/home/me/zlib-build"}):
            m = load_manifest(path)

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.LOCAL)
        self.assertEqual(m.dependencies[0].ref.value, "/home/me/zlib-build")

    def test_dep_override_windows_path(self) -> None:
        """NANVIX_VERSION_ZLIB=C:\\path -> RefKind.LOCAL."""
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = "1.0.0"\n'
        )

        with patch.dict(os.environ, {"NANVIX_VERSION_ZLIB": "C:\\Users\\me\\zlib"}):
            m = load_manifest(path)

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.LOCAL)
        self.assertEqual(m.dependencies[0].ref.value, "C:\\Users\\me\\zlib")

    def test_dep_override_relative_path(self) -> None:
        """NANVIX_VERSION_ZLIB=./local-build -> RefKind.LOCAL."""
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = "1.0.0"\n'
        )

        with patch.dict(os.environ, {"NANVIX_VERSION_ZLIB": "./local-build"}):
            m = load_manifest(path)

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.LOCAL)
        self.assertEqual(m.dependencies[0].ref.value, "./local-build")

    def test_dep_override_version_string_not_local(self) -> None:
        """Non-path env var preserves original RefKind."""
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = "1.0.0"\n'
        )

        with patch.dict(os.environ, {"NANVIX_VERSION_ZLIB": "env_sha"}):
            m = load_manifest(path)

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.VERSION)

    def test_sysroot_override_unix_path(self) -> None:
        """NANVIX_VERSION=/path/to/sysroot -> RefKind.LOCAL sysroot."""
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = "1.0.0"\n'
        )

        with patch.dict(os.environ, {"NANVIX_VERSION": "/opt/nanvix/sysroot"}):
            m = load_manifest(path)

        self.assertEqual(m.sysroot_ref.kind, RefKind.LOCAL)
        self.assertEqual(m.sysroot_ref.value, "/opt/nanvix/sysroot")

    def test_sysroot_override_version_string_not_local(self) -> None:
        """Non-path NANVIX_VERSION preserves original RefKind."""
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
        )

        with patch.dict(os.environ, {"NANVIX_VERSION": "override_sha"}):
            m = load_manifest(path)

        # Sysroot uses RefKind.TAG (not VERSION) — the original kind from the manifest.
        self.assertEqual(m.sysroot_ref.kind, RefKind.TAG)
        self.assertEqual(m.sysroot_ref.value, "override_sha")

    def test_local_dep_not_suffixed(self) -> None:
        """LOCAL deps skip suffix; adjacent VERSION deps are suffixed."""
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = "1.0.0"\n'
            'openssl = "2.0.0"\n'
        )

        with patch.dict(os.environ, {"NANVIX_VERSION_ZLIB": "/home/me/zlib-build"}):
            m = load_manifest(path)

        # LOCAL ref value must be the raw path — no suffix appended.
        zlib = next(d for d in m.dependencies if d.name == "zlib")
        self.assertEqual(zlib.ref.kind, RefKind.LOCAL)
        self.assertEqual(zlib.ref.value, "/home/me/zlib-build")
        self.assertNotIn("-nanvix-", str(zlib.ref.value))
        # Adjacent VERSION dep IS suffixed normally.
        openssl = next(d for d in m.dependencies if d.name == "openssl")
        self.assertEqual(openssl.ref.kind, RefKind.VERSION)
        self.assertEqual(openssl.ref.value, "2.0.0-nanvix-0.12.257")

    def test_local_sysroot_skips_suffix_loop(self) -> None:
        """When NANVIX_VERSION is a path, VERSION deps are NOT suffixed."""
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
            "[dependencies]\n"
            'zlib = "4.5.6"\n'
        )

        with patch.dict(os.environ, {"NANVIX_VERSION": "/opt/nanvix/sysroot"}):
            m = load_manifest(path)

        # Sysroot is LOCAL — suffix loop must be skipped entirely.
        # Without the guard, deps would get "-nanvix-/opt/nanvix/sysroot".
        self.assertEqual(m.sysroot_ref.kind, RefKind.LOCAL)
        self.assertEqual(m.dependencies[0].ref.kind, RefKind.VERSION)
        self.assertEqual(m.dependencies[0].ref.value, "4.5.6")
>>>>>>> f129376 ([tests] F: Add tests for RefKind.LOCAL)


if __name__ == "__main__":
    unittest.main()
