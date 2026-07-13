# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# pyright: reportPrivateUsage=false

"""Tests for pure atomic standalone update commands."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tomllib
import unittest
import zipfile
from pathlib import Path
from typing import cast
from unittest.mock import patch

import nanvix_zutil.updater as updater
from nanvix_zutil.commands import update_nanvix, update_zutils
from nanvix_zutil.lockfile import Lockfile, LockfileMetadata, read_lockfile
from nanvix_zutil.updater import UpdateError, atomic_write_candidates
from tests.test_sdk import make_sdk_contract

_OLD_DIGEST = f"sha256:{'e' * 64}"
_OLD_IMAGE_NAME = "ghcr.io/nanvix/nanvix-sdk-c-clang"
_OLD_IMAGE = f"{_OLD_IMAGE_NAME}@{_OLD_DIGEST}"


def template_source(root: Path, target: str = "v0.15.0") -> Path:
    """Create a complete local zutils templates source."""
    source = root / "source-templates"
    source.mkdir()
    (source / ".zutils-version").write_text(f"{target}\n")
    (source / "z").write_text('#!/usr/bin/env bash\nexec ./z.sh "$@"\n')
    (source / "z.sh").write_text("#!/usr/bin/env bash\necho zutils\n")
    (source / "z.ps1").write_text("Write-Output 'zutils'\n")
    (source / ".gitignore").write_text(
        "venv\n"
        "cache\n"
        ".nanvix-zutil-update.lock\n"
        ".nanvix-zutil-update-journal.json\n"
        ".*.nanvix-zutil-*\n"
    )
    (source / "nanvix-ci.yml").write_text(
        "jobs:\n  ci:\n    uses: nanvix/workflows/.github/workflows/nanvix-ci.yml@v1\n"
    )
    return source


def make_consumer(root: Path, *, newline: str = "\n") -> Path:
    """Create a minimal canonical SDK consumer fixture."""
    nanvix = root / ".nanvix"
    workflow = root / ".github" / "workflows"
    nanvix.mkdir(parents=True, exist_ok=True)
    workflow.mkdir(parents=True, exist_ok=True)
    manifest = newline.join(
        (
            "[package]",
            'name = "test"',
            'version = "1.0.0"',
            'nanvix-version = "0.19.0"',
            "",
            "[toolchain]",
            'kind = "nanvix-sdk"',
            'provider = "c-clang"',
            'sdk-version = "v0.19.0-sdk.2"',
            f'sdk-image = "{_OLD_IMAGE_NAME}"',
            f'sdk-digest = "{_OLD_DIGEST}"',
            "",
        )
    )
    (nanvix / "nanvix.toml").write_bytes(manifest.encode())
    (nanvix / "z.py").write_text("from nanvix_zutil import ZScript\n")
    (workflow / "nanvix-ci.yml").write_bytes(
        (
            f"jobs:{newline}"
            f"  ci:{newline}"
            f"    with:{newline}"
            f"      docker-image: >-{newline}"
            f"        {_OLD_IMAGE}{newline}"
            f"      caller-event-name: pull_request{newline}"
        ).encode()
    )
    (root / ".zutils-version").write_bytes(f"v0.14.0{newline}".encode())
    (root / "z").write_bytes(f"#!/usr/bin/env bash{newline}echo old{newline}".encode())
    (root / "z.sh").write_bytes(
        f"#!/usr/bin/env bash{newline}echo old{newline}".encode()
    )
    (root / "z.ps1").write_bytes(f"Write-Output 'old'{newline}".encode())
    (nanvix / ".gitignore").write_bytes(f"old{newline}".encode())
    (root / "z").chmod(0o755)
    (root / "z.sh").chmod(0o755)
    contract = root / "sdk-release.json"
    contract.write_text(json.dumps(make_sdk_contract()))
    return contract


def nanvix_args(target: str | None, **overrides: object) -> argparse.Namespace:
    """Build update-nanvix command arguments."""
    values: dict[str, object] = {
        "to": target,
        "dry_run": False,
        "check": False,
        "output_format": "json",
        "output": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def zutils_args(
    target: str,
    templates_dir: Path,
    **overrides: object,
) -> argparse.Namespace:
    """Build update-zutils command arguments."""
    values: dict[str, object] = {
        "to": target,
        "templates_dir": templates_dir,
        "dry_run": False,
        "check": False,
        "output_format": "json",
        "output": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class TestUpdateNanvix(unittest.TestCase):
    """Coherent manifest/lock updates and blocked outcomes."""

    def setUp(self) -> None:
        self.contract_data = make_sdk_contract()
        self.verify_patcher = patch(
            "nanvix_zutil.commands.update_nanvix.verify_sdk_image"
        )
        self.resolve_patcher = patch("nanvix_zutil.commands.update_nanvix.resolve")
        self.verify = self.verify_patcher.start()
        self.resolve = self.resolve_patcher.start()
        contract = update_nanvix.load_sdk_target(
            json.dumps(self.contract_data), Path.cwd()
        )
        assert contract is not None
        self.contract = contract
        self.resolve.return_value = Lockfile(
            LockfileMetadata(
                manifest_hash="sha256:candidate",
                nanvix_zutil_version="0.15.0",
                sdk=contract.provenance(),
            )
        )

    def tearDown(self) -> None:
        self.resolve_patcher.stop()
        self.verify_patcher.stop()

    def test_full_sdk_update_and_noop(self) -> None:
        root = Path.cwd()
        contract = make_consumer(root)
        result, code = update_nanvix._run(nanvix_args(contract.name))
        self.assertEqual(code, 0)
        self.assertEqual(result.status, "updated")
        self.assertEqual(
            result.changed_files,
            (
                ".github/workflows/nanvix-ci.yml",
                ".nanvix/nanvix.lock",
                ".nanvix/nanvix.toml",
            ),
        )
        manifest = (root / ".nanvix/nanvix.toml").read_text()
        self.assertIn('kind = "nanvix-sdk"', manifest)
        self.assertIn('sdk-version = "v0.20.0-sdk.1"', manifest)
        self.assertNotIn("NANVIX_SDK_IMAGE", (root / ".nanvix/z.py").read_text())
        workflow = (root / ".github/workflows/nanvix-ci.yml").read_text()
        self.assertNotIn("docker-image:", workflow)
        self.assertNotIn(_OLD_IMAGE, workflow)
        self.assertIn("caller-event-name: pull_request", workflow)
        self.assertEqual(
            read_lockfile(root / ".nanvix/nanvix.lock").metadata.sdk,
            self.contract.provenance(),
        )
        self.assertIsNotNone(result.old)
        assert result.new is not None
        self.assertEqual(result.new["sdk_version"], "v0.20.0-sdk.1")
        again, code = update_nanvix._run(nanvix_args(contract.name))
        self.assertEqual(code, 0)
        self.assertEqual(again.status, "up-to-date")

    def test_check_and_dry_run_do_not_write(self) -> None:
        root = Path.cwd()
        contract = make_consumer(root)
        original = (root / ".nanvix/nanvix.toml").read_bytes()
        checked, code = update_nanvix._run(nanvix_args(contract.name, check=True))
        self.assertEqual((checked.status, code), ("outdated", 1))
        dry, code = update_nanvix._run(nanvix_args(contract.name, dry_run=True))
        self.assertEqual((dry.status, code), ("would-update", 0))
        self.assertEqual((root / ".nanvix/nanvix.toml").read_bytes(), original)
        self.assertFalse((root / ".nanvix/nanvix.lock").exists())

    def test_crlf_manifest_and_workflow_are_preserved(self) -> None:
        root = Path.cwd()
        contract = make_consumer(root, newline="\r\n")
        update_nanvix._run(nanvix_args(contract.name))
        for relative in (
            ".nanvix/nanvix.toml",
            ".github/workflows/nanvix-ci.yml",
        ):
            content = (root / relative).read_bytes()
            self.assertIn(b"\r\n", content)
            self.assertNotIn(b"\n", content.replace(b"\r\n", b""))

    def test_transitional_marker_is_optional_and_checked(self) -> None:
        root = Path.cwd()
        contract = make_consumer(root)
        marker = root / ".nanvix/z.py"
        marker.write_text(
            f'NANVIX_SDK_IMAGE = "{_OLD_IMAGE}"\n' "from nanvix_zutil import ZScript\n"
        )
        result, _code = update_nanvix._run(nanvix_args(contract.name))
        self.assertIn(".nanvix/z.py", result.changed_files)
        self.assertIn(self.contract.image.ref, marker.read_text())

    def test_workflow_call_input_definition_is_preserved(self) -> None:
        """Only caller values, not reusable-workflow input schemas, are removed."""
        workflow = (
            "on:\n"
            "  workflow_call:\n"
            "    inputs:\n"
            "      docker-image:\n"
            "        type: string\n"
            "jobs:\n"
            "  ci:\n"
            "    with:\n"
            f"      docker-image: {_OLD_IMAGE}\n"
        )
        candidate = update_nanvix._remove_docker_image_inputs(workflow)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertIn("      docker-image:\n        type: string\n", candidate)
        self.assertNotIn(_OLD_IMAGE, candidate)

    def test_dependencies_are_retargeted_before_strict_resolution(self) -> None:
        root = Path.cwd()
        contract = make_consumer(root)
        manifest_path = root / ".nanvix/nanvix.toml"
        manifest_path.write_text(
            manifest_path.read_text() + '\n[dependencies]\nzlib = "1.3.1"\n'
        )
        update_nanvix._run(nanvix_args(contract.name, dry_run=True))
        candidate = self.resolve.call_args.args[0]
        self.assertEqual(
            candidate.dependencies[0].ref.value,
            "1.3.1-nanvix-0.20.0-sdk.1",
        )
        self.assertTrue(self.resolve.call_args.kwargs["strict"])
        self.assertEqual(
            self.resolve.call_args.kwargs["manifest_content"],
            update_nanvix._update_manifest(manifest_path.read_bytes(), self.contract),
        )

    def test_nested_transitional_toolchain_is_fully_canonicalized(self) -> None:
        old = (
            "[package]\n"
            'name = "test"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.19.0"\n'
            "\n[toolchain]\n"
            'type = "sdk"\n'
            "\n[toolchain.sdk]\n"
            'version = "v0.19.0-sdk.2"\n'
            'provider = "c-clang"\n'
            f'image = "{_OLD_IMAGE}"\n'
            "\n[dependencies]\n"
            'zlib = "1.3.1"\n'
        ).encode()
        candidate = update_nanvix._update_manifest(old, self.contract)
        data = tomllib.loads(candidate.decode())
        self.assertNotIn("sdk", data["toolchain"])
        self.assertEqual(
            data["toolchain"]["kind"],
            "nanvix-sdk",
        )

    def test_blocked_result_writes_nothing(self) -> None:
        root = Path.cwd()
        contract = make_consumer(root)
        original = (root / ".nanvix/nanvix.toml").read_bytes()
        self.resolve.return_value = update_nanvix.BlockedResolution(
            status="blocked",
            package="zlib",
            repo="nanvix/zlib",
            requested_tag="1.3.1-nanvix-0.20.0-sdk.1",
            sdk_version="v0.20.0-sdk.1",
            reason="provenance-lock-missing",
        )
        result, code = update_nanvix._run(nanvix_args(contract.name))
        self.assertEqual((result.status, code), ("blocked", 3))
        self.assertEqual(result.changed_files, ())
        assert result.blocker is not None
        self.assertEqual(result.blocker["reason"], "provenance-lock-missing")
        self.assertEqual((root / ".nanvix/nanvix.toml").read_bytes(), original)
        self.assertFalse((root / ".nanvix/nanvix.lock").exists())

    def test_changed_sdk_with_derived_build_image_is_blocked(self) -> None:
        root = Path.cwd()
        contract = make_consumer(root)
        manifest = root / ".nanvix/nanvix.toml"
        manifest.write_text(
            manifest.read_text()
            + 'build-image = "ghcr.io/nanvix/derived-builder"\n'
            + f'build-digest = "sha256:{"f" * 64}"\n'
        )
        original = manifest.read_bytes()
        result, code = update_nanvix._run(nanvix_args(contract.name))
        self.assertEqual((result.status, code), ("blocked", 3))
        assert result.blocker is not None
        self.assertEqual(
            result.blocker["reason"],
            "derived-build-image-update-required",
        )
        self.assertEqual(manifest.read_bytes(), original)

    def test_default_and_explicit_tag_resolve_authoritative_release(self) -> None:
        root = Path.cwd()
        make_consumer(root)
        with patch(
            "nanvix_zutil.commands.update_nanvix.resolve_sdk_release",
            return_value=self.contract,
        ) as resolver:
            self.resolve.return_value = Lockfile(
                LockfileMetadata("sha256:x", "0.15.0", sdk=self.contract.provenance())
            )
            update_nanvix._run(nanvix_args(None, dry_run=True))
            resolver.assert_called_with("latest", gh_token=None, verify_image=True)
            update_nanvix._run(nanvix_args("v0.20.0-sdk.1", dry_run=True))
            self.assertEqual(resolver.call_args.args[0], "v0.20.0-sdk.1")

    def test_json_output_file(self) -> None:
        root = Path.cwd()
        contract = make_consumer(root)
        with patch.object(
            sys,
            "argv",
            [
                "update-nanvix",
                "--to",
                contract.name,
                "--dry-run",
                "--output-format",
                "json",
                "--output",
                "result.json",
            ],
        ):
            with self.assertRaises(SystemExit) as context:
                update_nanvix.main()
        self.assertEqual(context.exception.code, 0)
        payload: object = json.loads((root / "result.json").read_text())
        self.assertIsInstance(payload, dict)
        assert isinstance(payload, dict)
        payload_data = cast("dict[str, object]", payload)
        self.assertEqual(payload_data["status"], "would-update")
        self.assertIn("old", payload_data)
        self.assertIn("new", payload_data)

    def test_interrupted_manifest_is_recovered_before_parsing(self) -> None:
        root = Path.cwd()
        contract = make_consumer(root)
        manifest = root / ".nanvix/nanvix.toml"
        original = manifest.read_bytes()
        manifest.write_text("not valid toml")
        journal = root / ".nanvix/.nanvix-zutil-update-journal.json"
        journal.write_text(
            json.dumps(
                {
                    "state": "installing",
                    "files": [
                        {
                            "path": ".nanvix/nanvix.toml",
                            "mode": 0o644,
                            "content": base64.b64encode(original).decode(),
                        }
                    ],
                }
            )
        )
        result, code = update_nanvix._run(nanvix_args(contract.name, dry_run=True))
        self.assertEqual((result.status, code), ("would-update", 0))
        self.assertFalse(journal.exists())
        self.assertEqual(manifest.read_bytes(), original)


class TestUpdateZutils(unittest.TestCase):
    """Verified template updates preserve policies and exact allowlists."""

    def test_shipped_gitignore_matches_canonical_config(self) -> None:
        """Bootstrap updates must preserve every generated-artifact pattern."""
        root = Path(__file__).resolve().parent.parent
        self.assertEqual(
            (root / "templates/.gitignore").read_bytes(),
            (root / "src/nanvix_zutil/configs/.gitignore").read_bytes(),
        )

    def test_update_and_noop_exact_allowlist(self) -> None:
        root = Path.cwd()
        make_consumer(root)
        source = template_source(root)
        result, code = update_zutils._run(zutils_args("v0.15.0", source))
        self.assertEqual(code, 0)
        self.assertEqual(
            result.changed_files,
            (
                ".nanvix/.gitignore",
                ".zutils-version",
                "z",
                "z.ps1",
                "z.sh",
            ),
        )
        again, _code = update_zutils._run(zutils_args("0.15.0", source))
        self.assertEqual(again.status, "up-to-date")
        gitignore = (root / ".nanvix/.gitignore").read_text()
        self.assertNotIn("nanvix.lock\n", gitignore)
        self.assertIn(".nanvix-zutil-update.lock", gitignore)

    def test_check_and_dry_run(self) -> None:
        root = Path.cwd()
        make_consumer(root)
        source = template_source(root)
        checked, code = update_zutils._run(zutils_args("v0.15.0", source, check=True))
        self.assertEqual((checked.status, code), ("outdated", 1))
        dry, code = update_zutils._run(zutils_args("v0.15.0", source, dry_run=True))
        self.assertEqual((dry.status, code), ("would-update", 0))
        self.assertEqual((root / ".zutils-version").read_text(), "v0.14.0\n")

    def test_crlf_and_executable_modes_are_preserved(self) -> None:
        root = Path.cwd()
        make_consumer(root, newline="\r\n")
        source = template_source(root)
        old_mode = stat.S_IMODE((root / "z").stat().st_mode)
        update_zutils._run(zutils_args("v0.15.0", source))
        for relative in (".zutils-version", "z", "z.sh", "z.ps1"):
            content = (root / relative).read_bytes()
            self.assertIn(b"\r\n", content)
            self.assertNotIn(b"\n", content.replace(b"\r\n", b""))
        self.assertEqual(stat.S_IMODE((root / "z").stat().st_mode), old_mode)
        if os.name != "nt":
            self.assertTrue((root / "z").stat().st_mode & stat.S_IXUSR)

    def test_new_shell_bootstrappers_are_executable(self) -> None:
        root = Path.cwd()
        make_consumer(root)
        source = template_source(root)
        probe = root / "mode-probe"
        probe.write_bytes(b"probe")
        probe.chmod(0o755)
        expected_mode = stat.S_IMODE(probe.stat().st_mode)
        probe.unlink()
        (root / "z").unlink()
        (root / "z.sh").unlink()
        update_zutils._run(zutils_args("v0.15.0", source))
        self.assertEqual(
            stat.S_IMODE((root / "z").stat().st_mode),
            expected_mode,
        )
        self.assertEqual(
            stat.S_IMODE((root / "z.sh").stat().st_mode),
            expected_mode,
        )
        if os.name != "nt":
            self.assertTrue((root / "z").stat().st_mode & stat.S_IXUSR)

    def test_interrupted_pin_is_recovered_before_validation(self) -> None:
        root = Path.cwd()
        make_consumer(root)
        source = template_source(root)
        pin = root / ".zutils-version"
        original = pin.read_bytes()
        pin.write_text("broken")
        journal = root / ".nanvix/.nanvix-zutil-update-journal.json"
        journal.write_text(
            json.dumps(
                {
                    "state": "installing",
                    "files": [
                        {
                            "path": ".zutils-version",
                            "mode": 0o644,
                            "content": base64.b64encode(original).decode(),
                        }
                    ],
                }
            )
        )
        result, code = update_zutils._run(zutils_args("v0.15.0", source, dry_run=True))
        self.assertEqual((result.status, code), ("would-update", 0))
        self.assertEqual(pin.read_bytes(), original)
        self.assertFalse(journal.exists())

    def test_malformed_or_wrong_templates_fail(self) -> None:
        root = Path.cwd()
        make_consumer(root)
        source = template_source(root, target="v0.14.0")
        with self.assertRaises(UpdateError):
            update_zutils._run(zutils_args("v0.15.0", source))
        (source / "z.ps1").unlink()
        with self.assertRaises(UpdateError):
            update_zutils._run(zutils_args("v0.14.0", source))

    def test_archive_modes_and_malformed_archive(self) -> None:
        source = template_source(Path.cwd())
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name in update_zutils._SOURCE_NAMES:
                archive.writestr(f"templates/{name}", (source / name).read_bytes())
        templates = update_zutils._templates_from_archive(stream.getvalue(), "v0.15.0")
        self.assertEqual(set(templates), set(update_zutils._SOURCE_NAMES))
        with self.assertRaises(UpdateError):
            update_zutils._templates_from_archive(b"not-a-zip", "v0.15.0")

    def test_default_source_uses_exact_release_archive(self) -> None:
        source = template_source(Path.cwd())
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name in update_zutils._SOURCE_NAMES:
                archive.writestr(name, (source / name).read_bytes())
        release: dict[str, object] = {
            "tag_name": "v0.15.0",
            "assets": [
                {
                    "name": "templates.zip",
                    "browser_download_url": "https://example.invalid/templates.zip",
                    "digest": (
                        "sha256:" + hashlib.sha256(stream.getvalue()).hexdigest()
                    ),
                }
            ],
        }
        with (
            patch(
                "nanvix_zutil.commands.update_zutils.github.resolve_release",
                return_value=release,
            ) as resolver,
            patch(
                "nanvix_zutil.commands.update_zutils._download_archive",
                return_value=stream.getvalue(),
            ) as download,
            patch.dict(os.environ, {"GH_TOKEN": "read-token"}, clear=False),
        ):
            templates = update_zutils._resolve_templates("v0.15.0", None)
        resolver.assert_called_once_with(
            "nanvix/zutils", "v0.15.0", gh_token="read-token"
        )
        download.assert_called_once_with(
            "https://example.invalid/templates.zip", "read-token"
        )
        self.assertEqual(
            templates[".zutils-version"],
            (source / ".zutils-version").read_bytes(),
        )

    def test_release_tag_or_asset_skew_fails(self) -> None:
        with self.assertRaises(UpdateError):
            update_zutils._release_asset(
                {"tag_name": "v0.14.0", "assets": []},
                "v0.15.0",
            )
        with self.assertRaises(UpdateError):
            update_zutils._verify_archive_digest(
                b"archive",
                f"sha256:{'0' * 64}",
            )
        with self.assertRaises(UpdateError):
            update_zutils._release_asset(
                {"tag_name": "v0.15.0", "assets": []},
                "v0.15.0",
            )

    def test_zutils_source_allowlist(self) -> None:
        root = Path.cwd()
        (root / "templates").mkdir()
        (root / "templates/.zutils-version").write_text("v0.15.0\n")
        (root / "examples").mkdir()
        maps = update_zutils._target_maps(root)
        targets = {
            relative.as_posix()
            for target_map in maps
            for relative in target_map.values()
        }
        self.assertIn("templates/z", targets)
        self.assertIn("examples/bin-hello/.nanvix/.gitignore", targets)
        self.assertIn("examples/lib-hello/z.ps1", targets)


class TestAtomicUpdates(unittest.TestCase):
    """Multi-file failures restore bytes and executable modes."""

    def test_failure_rolls_back_bytes_and_modes(self) -> None:
        root = Path.cwd()
        first = root / "first"
        second = root / "second"
        first.write_bytes(b"old-first")
        second.write_bytes(b"old-second")
        first.chmod(0o755)
        first_mode = stat.S_IMODE(first.stat().st_mode)
        real_replace = os.replace
        calls = 0

        def fail_second(source: Path, target: Path) -> None:
            nonlocal calls
            if ".nanvix-zutil-update-journal" in str(target):
                real_replace(source, target)
                return
            calls += 1
            if calls == 2:
                raise OSError("injected")
            real_replace(source, target)

        with patch("nanvix_zutil.updater.os.replace", side_effect=fail_second):
            with self.assertRaises(UpdateError):
                atomic_write_candidates(
                    root,
                    {Path("first"): b"new-first", Path("second"): b"new-second"},
                    {},
                )
        self.assertEqual(first.read_bytes(), b"old-first")
        self.assertEqual(second.read_bytes(), b"old-second")
        self.assertEqual(stat.S_IMODE(first.stat().st_mode), first_mode)
        self.assertTrue((root / ".nanvix/.nanvix-zutil-update.lock").exists())
        self.assertFalse((root / ".nanvix/.nanvix-zutil-update-journal.json").exists())
        self.assertFalse((root / ".nanvix-zutil-update.lock").exists())
        self.assertFalse((root / ".nanvix-zutil-update-journal.json").exists())

    def test_directory_fsync_failure_rolls_back_replaced_target(self) -> None:
        root = Path.cwd()
        first = root / "first"
        second = root / "second"
        first.write_bytes(b"old-first")
        second.write_bytes(b"old-second")
        calls = 0
        real_fsync_directory = updater._fsync_directory

        def fail_after_first_replace(path: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("directory fsync failed")
            real_fsync_directory(path)

        with (
            patch(
                "nanvix_zutil.updater._fsync_directory",
                side_effect=fail_after_first_replace,
            ),
            self.assertRaises(UpdateError),
        ):
            atomic_write_candidates(
                root,
                {Path("first"): b"new-first", Path("second"): b"new-second"},
                {},
            )
        self.assertEqual(first.read_bytes(), b"old-first")
        self.assertEqual(second.read_bytes(), b"old-second")
        self.assertFalse((root / ".nanvix/.nanvix-zutil-update-journal.json").exists())

    def test_rollback_sidecar_is_fsynced_before_replace(self) -> None:
        root = Path.cwd()
        target = root / "target"
        target.write_bytes(b"new")
        events: list[str] = []
        real_replace = os.replace
        real_fsync = os.fsync

        def record_replace(source: Path, destination: Path) -> None:
            events.append("replace")
            real_replace(source, destination)

        def record_fsync(descriptor: int) -> None:
            events.append("fsync")
            real_fsync(descriptor)

        with (
            patch("nanvix_zutil.updater.os.replace", side_effect=record_replace),
            patch("nanvix_zutil.updater.os.fsync", side_effect=record_fsync),
        ):
            updater._restore_snapshot(target, b"old", 0o755)
        self.assertEqual(target.read_bytes(), b"old")
        self.assertLess(events.index("fsync"), events.index("replace"))

    def test_concurrent_owner_blocks_and_context_exit_releases(self) -> None:
        root = Path.cwd()
        target = root / "target"
        target.write_bytes(b"old")
        lock = root / ".nanvix/.nanvix-zutil-update.lock"
        with updater.update_transaction(root):
            with self.assertRaises(UpdateError):
                atomic_write_candidates(
                    root,
                    {Path("target"): b"new"},
                    {},
                )
        changed = atomic_write_candidates(
            root,
            {Path("target"): b"new"},
            {},
        )
        self.assertEqual(changed, ("target",))
        self.assertEqual(target.read_bytes(), b"new")
        self.assertTrue(lock.exists())
        self.assertFalse((root / ".nanvix/.nanvix-zutil-update-journal.json").exists())
        self.assertFalse((root / ".nanvix-zutil-update.lock").exists())

    def test_lock_releases_when_owner_process_exits(self) -> None:
        root = Path.cwd()
        code = (
            "import sys,time\n"
            "from pathlib import Path\n"
            "from nanvix_zutil.updater import update_transaction\n"
            "with update_transaction(Path(sys.argv[1])):\n"
            " print('locked', flush=True)\n"
            " time.sleep(0.5)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(root)],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        self.assertEqual(process.stdout.readline().strip(), "locked")
        with self.assertRaises(UpdateError):
            with updater.update_transaction(root):
                pass
        self.assertEqual(process.wait(timeout=5), 0)
        with updater.update_transaction(root):
            pass

    def test_zutils_source_uses_root_local_transient_state(self) -> None:
        root = Path.cwd()
        (root / "templates").mkdir()
        (root / "templates/.zutils-version").write_text("v0.15.0\n")
        (root / "examples").mkdir()
        target = root / "target"
        target.write_bytes(b"old")
        changed = atomic_write_candidates(
            root,
            {Path("target"): b"new"},
            {},
        )
        self.assertEqual(changed, ("target",))
        self.assertTrue((root / ".nanvix-zutil-update.lock").exists())
        self.assertFalse((root / ".nanvix-zutil-update-journal.json").exists())

    def test_recovery_rejects_symlink_escape(self) -> None:
        root = Path.cwd()
        outside = root.parent
        (root / "link").symlink_to(outside, target_is_directory=True)
        journal = root / ".nanvix/.nanvix-zutil-update-journal.json"
        journal.write_text(
            json.dumps(
                {
                    "state": "installing",
                    "files": [
                        {
                            "path": "link/escape",
                            "mode": 0o644,
                            "content": base64.b64encode(b"secret").decode(),
                        }
                    ],
                }
            )
        )
        with self.assertRaises(UpdateError):
            with updater.update_transaction(root):
                pass
        self.assertTrue(journal.exists())
        self.assertFalse((outside / "escape").exists())

    def test_incomplete_rollback_retains_journal(self) -> None:
        root = Path.cwd()
        first = root / "first"
        second = root / "second"
        first.write_bytes(b"old-first")
        second.write_bytes(b"old-second")
        real_replace = os.replace
        installs = 0

        def fail_second(source: Path, target: Path) -> None:
            nonlocal installs
            if ".nanvix-zutil-update-journal" in str(target):
                real_replace(source, target)
                return
            installs += 1
            if installs == 2:
                raise OSError("read-only destination")
            real_replace(source, target)

        with (
            patch("nanvix_zutil.updater.os.replace", side_effect=fail_second),
            patch(
                "nanvix_zutil.updater._restore_snapshot",
                side_effect=PermissionError("read-only rollback"),
            ),
            self.assertRaises(UpdateError) as context,
        ):
            atomic_write_candidates(
                root,
                {Path("first"): b"new-first", Path("second"): b"new-second"},
                {},
            )
        self.assertIn("rollback is incomplete", str(context.exception))
        self.assertTrue((root / ".nanvix/.nanvix-zutil-update-journal.json").exists())

    def test_journal_write_is_atomic_and_fsynced(self) -> None:
        root = Path.cwd()
        journal = root / ".nanvix/journal.json"
        with patch("nanvix_zutil.updater.os.fsync", wraps=os.fsync) as fsync:
            updater._write_journal(
                journal,
                {"state": "installing", "files": []},
            )
        self.assertEqual(json.loads(journal.read_text())["state"], "installing")
        self.assertGreaterEqual(fsync.call_count, 1 if os.name == "nt" else 2)
        self.assertEqual(
            list(journal.parent.glob(f".{journal.name}.*.new")),
            [],
        )

    def test_journal_is_retained_when_delete_fsync_fails(self) -> None:
        root = Path.cwd()
        journal = root / ".nanvix/journal.json"
        data: dict[str, object] = {"state": "committed", "files": []}
        journal.write_text(json.dumps(data))
        with (
            patch(
                "nanvix_zutil.updater._fsync_directory",
                side_effect=OSError("durability uncertain"),
            ),
            self.assertRaises(OSError),
        ):
            updater._remove_journal(journal, data)
        self.assertTrue(journal.exists())
        self.assertEqual(json.loads(journal.read_text())["state"], "committed")

    def test_fsync_file_uses_read_write_descriptor(self) -> None:
        path = Path.cwd() / "file"
        path.write_bytes(b"data")
        with (
            patch("nanvix_zutil.updater.os.open", return_value=123) as opened,
            patch("nanvix_zutil.updater.os.fsync") as fsync,
            patch("nanvix_zutil.updater.os.close") as close,
        ):
            updater._fsync_file(path)
        opened.assert_called_once_with(path, os.O_RDWR)
        fsync.assert_called_once_with(123)
        close.assert_called_once_with(123)

    def test_windows_lock_path_never_uses_kill(self) -> None:
        root = Path.cwd()
        with (
            patch("nanvix_zutil.updater.os.name", "nt"),
            patch(
                "nanvix_zutil.updater._windows_lock",
            ) as windows_lock,
            patch("nanvix_zutil.updater._windows_unlock") as windows_unlock,
            patch("nanvix_zutil.updater.os.kill") as kill,
        ):
            with updater.update_transaction(root):
                pass
        windows_lock.assert_called_once()
        windows_unlock.assert_called_once()
        kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
