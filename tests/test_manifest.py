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
from nanvix_zutil.manifest import load_manifest


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
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = ""\n'
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
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "abc123"\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_latest_nanvix_version_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "latest"\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

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
        path.write_text(
            "[package]\n" 'version = "1.0.0"\n' 'nanvix-version = "0.12.257"\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_missing_version_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text(
            "[package]\n" 'name = "myapp"\n' 'nanvix-version = "0.12.257"\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest(path)

        self.assertEqual(ctx.exception.code, 2)

    def test_missing_nanvix_version_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text("[package]\n" 'name = "myapp"\n' 'version = "1.0.0"\n')

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
        )

        with patch.dict(os.environ, {"NANVIX_VERSION": "override_sha"}):
            m = load_manifest(path)

        self.assertEqual(m.sysroot_ref.value, "override_sha")
        self.assertEqual(m.dependencies[0].ref.value, "1.0.0-nanvix-override_sha")

    def test_nanvix_version_dep_overrides_dep(self) -> None:
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
        )

        with patch.dict(
            os.environ,
            {"NANVIX_VERSION": "env_nanvix", "NANVIX_VERSION_ZLIB": "env_zlib"},
        ):
            m = load_manifest(path)

        self.assertEqual(m.sysroot_ref.value, "env_nanvix")
        self.assertEqual(m.dependencies[0].ref.value, "env_zlib-nanvix-env_nanvix")


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


if __name__ == "__main__":
    unittest.main()
