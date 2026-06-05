# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.manifest."""

import unittest
from unittest.mock import patch

from nanvix_zutil import paths
from nanvix_zutil.buildroot import RefKind
from nanvix_zutil.manifest import is_local_path, load_manifest
from tests.testutils import make_toml


class TestLoadManifestFileNotFound(unittest.TestCase):
    """load_manifest exits 3 when the file does not exist."""

    def test_missing_file_exits_3(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 3)


class TestLoadManifestBasic(unittest.TestCase):
    """load_manifest parses a well-formed nanvix.toml."""

    def test_single_dependency_commitish(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": '{ commitish = "b7a6a3c" }'}))

        m = load_manifest()

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
        path = paths.manifest_path()
        path.write_text(make_toml(nanvix_version="1.2.3", deps={"zlib": '"4.5.6"'}))

        m = load_manifest()

        self.assertEqual(m.sysroot_ref.kind, RefKind.TAG)
        self.assertEqual(m.sysroot_ref.value, "1.2.3")
        self.assertEqual(m.dependencies[0].ref.kind, RefKind.VERSION)
        self.assertEqual(m.dependencies[0].ref.value, "4.5.6-nanvix-1.2.3")

    def test_single_dependency_version_table(self) -> None:
        path = paths.manifest_path()
        path.write_text(
            make_toml(nanvix_version="1.2.3", deps={"zlib": '{ version = "4.5.6" }'})
        )

        m = load_manifest()

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.VERSION)
        self.assertEqual(m.dependencies[0].ref.value, "4.5.6-nanvix-1.2.3")

    def test_single_dependency_tag(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": '{ tag = "675d8f2-nanvix-e63706b" }'}))

        m = load_manifest()

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.TAG)
        self.assertEqual(m.dependencies[0].ref.value, "675d8f2-nanvix-e63706b")

    def test_single_dependency_id(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": "{ id = 12345678 }"}))

        m = load_manifest()

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.ID)
        self.assertEqual(m.dependencies[0].ref.value, 12345678)

    def test_mixed_dependencies(self) -> None:
        path = paths.manifest_path()
        path.write_text(
            make_toml(
                nanvix_version="1.0.0",
                deps={"zlib": '{ commitish = "aaa" }', "openssl": '"2.0.0"'},
            )
        )

        m = load_manifest()

        self.assertEqual(m.sysroot_ref.value, "1.0.0")
        self.assertEqual(len(m.dependencies), 2)
        self.assertEqual(m.dependencies[0].ref.kind, RefKind.COMMITISH)
        self.assertEqual(m.dependencies[0].ref.value, "aaa")
        self.assertEqual(m.dependencies[1].ref.kind, RefKind.VERSION)
        self.assertEqual(m.dependencies[1].ref.value, "2.0.0-nanvix-1.0.0")

    def test_no_dependencies_section(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml())

        m = load_manifest()

        self.assertEqual(len(m.dependencies), 0)
        self.assertEqual(len(m.system_dependencies), 0)

    def test_empty_dependencies_section(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={}))

        m = load_manifest()

        self.assertEqual(len(m.dependencies), 0)

    def test_empty_system_dependencies_section(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(sys_deps={}))

        m = load_manifest()

        self.assertEqual(len(m.system_dependencies), 0)


class TestLoadManifestInvalidVersion(unittest.TestCase):
    """Invalid version specs are rejected."""

    def test_empty_string_nanvix_version_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(nanvix_version=""))

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_whitespace_nanvix_version_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(nanvix_version="has space"))

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_non_semver_nanvix_version_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(nanvix_version="abc123"))

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_latest_nanvix_version_accepted(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(nanvix_version="latest"))

        m = load_manifest()

        self.assertEqual(m.sysroot_ref.kind, RefKind.TAG)
        self.assertEqual(m.sysroot_ref.value, "latest")

    def test_latest_sysroot_skips_dep_suffix(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(nanvix_version="latest", deps={"zlib": '"1.3.1"'}))

        m = load_manifest()

        self.assertEqual(m.sysroot_ref.value, "latest")
        # Dep should NOT be suffixed — deferred to resolver
        self.assertEqual(m.dependencies[0].ref.value, "1.3.1")

    def test_nanvix_version_table_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = { tag = "v1.0.0" }\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_whitespace_dep_version_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": '"has space"'}))

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_empty_commitish_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": '{ commitish = "" }'}))

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_non_string_commitish_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": "{ commitish = 123 }"}))

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_multiple_specifier_keys_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": '{ version = "1.0", tag = "v1.0" }'}))

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_non_integer_id_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": '{ id = "abc" }'}))

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_unknown_table_key_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": '{ foo = "bar" }'}))

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)


class TestLoadManifestLatestWarning(unittest.TestCase):
    """'latest' emits a warning but is accepted for dependencies."""

    def test_latest_dep_version_warns(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": '"latest"'}))

        with patch("nanvix_zutil.manifest.log") as mock_log:
            m = load_manifest()

        mock_log.warning.assert_called()
        self.assertEqual(m.dependencies[0].ref.value, "latest-nanvix-0.12.257")


class TestLoadManifestPackageValidation(unittest.TestCase):
    """Required [package] keys are validated."""

    def test_missing_package_section_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text("")

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_missing_name_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text('[package]\nversion = "1.0.0"\nnanvix-version = "0.12.257"\n')

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_missing_version_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text('[package]\nname = "myapp"\nnanvix-version = "0.12.257"\n')

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_missing_nanvix_version_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text('[package]\nname = "myapp"\nversion = "1.0.0"\n')

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_empty_file_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text("")

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)


class TestLoadManifestMalformedToml(unittest.TestCase):
    """Invalid TOML syntax is rejected."""

    def test_invalid_toml_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text("this is not valid toml [[[")

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)


class TestLoadManifestAutoSuffix(unittest.TestCase):
    """Only VERSION refs are auto-suffixed with the nanvix version."""

    def test_version_string_dep_suffixed(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(nanvix_version="1.0.0", deps={"zlib": '"4.5.6"'}))

        m = load_manifest()

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.VERSION)
        self.assertEqual(m.dependencies[0].ref.value, "4.5.6-nanvix-1.0.0")

    def test_version_table_dep_suffixed(self) -> None:
        path = paths.manifest_path()
        path.write_text(
            make_toml(nanvix_version="1.0.0", deps={"zlib": '{ version = "4.5.6" }'})
        )

        m = load_manifest()

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.VERSION)
        self.assertEqual(m.dependencies[0].ref.value, "4.5.6-nanvix-1.0.0")

    def test_commitish_dep_not_suffixed(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": '{ commitish = "b7a6a3c" }'}))

        m = load_manifest()

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.COMMITISH)
        self.assertEqual(m.dependencies[0].ref.value, "b7a6a3c")

    def test_tag_dep_not_suffixed(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": '{ tag = "v1.0.0-nanvix-abc" }'}))

        m = load_manifest()

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.TAG)
        self.assertEqual(m.dependencies[0].ref.value, "v1.0.0-nanvix-abc")

    def test_id_dep_not_suffixed(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": "{ id = 99999 }"}))

        m = load_manifest()

        self.assertEqual(m.dependencies[0].ref.kind, RefKind.ID)
        self.assertEqual(m.dependencies[0].ref.value, 99999)

    def test_full_ref_with_nanvix_rejected(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": '"b7a6a3c-nanvix-fa06b88"'}))

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_multiple_version_deps_suffixed(self) -> None:
        path = paths.manifest_path()
        path.write_text(
            make_toml(
                nanvix_version="1.0.0",
                deps={"zlib": '"1.2.3"', "openssl": '"2.0.0"'},
            )
        )

        m = load_manifest()

        self.assertEqual(m.dependencies[0].ref.value, "1.2.3-nanvix-1.0.0")
        self.assertEqual(m.dependencies[1].ref.value, "2.0.0-nanvix-1.0.0")

    def test_system_deps_version_suffixed(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(sys_deps={"foobar": '"1.2.3"'}))

        m = load_manifest()

        self.assertEqual(m.system_dependencies[0].name, "foobar")
        self.assertEqual(m.system_dependencies[0].ref.kind, RefKind.VERSION)
        self.assertEqual(m.system_dependencies[0].ref.value, "1.2.3-nanvix-0.12.257")


class TestLoadManifestSystemDependencies(unittest.TestCase):
    """[system-dependencies] are parsed correctly."""

    def test_system_deps_parsed(self) -> None:
        path = paths.manifest_path()
        path.write_text(
            make_toml(
                sys_deps={"foobar": '"1.2.3"', "bazqux": '{ commitish = "deadbeef" }'},
            )
        )

        m = load_manifest()

        self.assertEqual(len(m.system_dependencies), 2)
        self.assertEqual(m.system_dependencies[0].name, "foobar")
        self.assertEqual(m.system_dependencies[0].repo, "nanvix/foobar")
        self.assertEqual(m.system_dependencies[0].ref.kind, RefKind.VERSION)
        self.assertEqual(m.system_dependencies[0].ref.value, "1.2.3-nanvix-0.12.257")
        self.assertEqual(m.system_dependencies[1].name, "bazqux")
        self.assertEqual(m.system_dependencies[1].ref.kind, RefKind.COMMITISH)
        self.assertEqual(m.system_dependencies[1].ref.value, "deadbeef")

    def test_both_dep_sections_parsed(self) -> None:
        path = paths.manifest_path()
        path.write_text(
            make_toml(
                deps={"zlib": '{ commitish = "aaa" }'},
                sys_deps={"foobar": '"1.2.3"'},
            )
        )

        m = load_manifest()

        self.assertEqual(len(m.dependencies), 1)
        self.assertEqual(len(m.system_dependencies), 1)


class TestLoadManifestTypeValidation(unittest.TestCase):
    """Non-string/non-table dependency values and non-table sections are rejected."""

    def test_integer_dependency_value_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": "123"}))

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_boolean_dependency_value_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(make_toml(deps={"zlib": "true"}))

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_dependencies_not_table_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(
            'dependencies = "not-a-table"\n'
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)

    def test_system_dependencies_not_table_exits_2(self) -> None:
        path = paths.manifest_path()
        path.write_text(
            "system-dependencies = 42\n"
            "[package]\n"
            'name = "myapp"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.12.257"\n'
        )

        with self.assertRaises(SystemExit) as ctx:
            load_manifest()

        self.assertEqual(ctx.exception.code, 2)


class TestIsLocalPath(unittest.TestCase):
    """is_local_path detects filesystem paths correctly."""

    def test_unix_absolute(self) -> None:
        self.assertTrue(is_local_path("/home/me/zlib-build"))

    def test_unix_root(self) -> None:
        self.assertTrue(is_local_path("/"))

    def test_windows_drive_backslash(self) -> None:
        self.assertTrue(is_local_path("C:\\Users\\me\\build"))

    def test_windows_drive_forward_slash(self) -> None:
        self.assertTrue(is_local_path("D:/builds/zlib"))

    def test_windows_lowercase_drive(self) -> None:
        self.assertTrue(is_local_path("c:\\builds"))

    def test_relative_dot_slash(self) -> None:
        self.assertTrue(is_local_path("./build"))

    def test_relative_dot_dot_slash(self) -> None:
        self.assertTrue(is_local_path("../build"))

    def test_semver_not_path(self) -> None:
        self.assertFalse(is_local_path("1.3.1"))

    def test_tag_not_path(self) -> None:
        self.assertFalse(is_local_path("v1.0.0"))

    def test_commitish_not_path(self) -> None:
        self.assertFalse(is_local_path("b7a6a3c"))

    def test_latest_not_path(self) -> None:
        self.assertFalse(is_local_path("latest"))

    def test_empty_not_path(self) -> None:
        self.assertFalse(is_local_path(""))


if __name__ == "__main__":
    unittest.main()
