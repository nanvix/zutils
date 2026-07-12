# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Pure, confined, atomic file-update helpers."""

from __future__ import annotations

import base64
import json
import os
import stat
import tomllib
from collections.abc import Callable
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from nanvix_zutil.sdk import SdkRelease, validate_sdk_release


class UpdateError(ValueError):
    """Raised when an update candidate violates a safety invariant."""


@dataclass(frozen=True)
class UpdateResult:
    """Deterministic result from an update command."""

    command: str
    status: str
    target: str
    changed_files: tuple[str, ...]
    old: dict[str, object] | None = None
    new: dict[str, object] | None = None
    blocker: dict[str, object] | None = None

    @property
    def changed(self) -> bool:
        """Return whether tracked target files differ."""
        return bool(self.changed_files)

    def to_dict(self) -> dict[str, object]:
        """Return a machine-readable result."""
        result: dict[str, object] = {
            "command": self.command,
            "status": self.status,
            "target": self.target,
            "changed": self.changed,
            "changed_files": list(self.changed_files),
        }
        if self.old is not None:
            result["old"] = self.old
        if self.new is not None:
            result["new"] = self.new
        if self.blocker is not None:
            result["blocker"] = self.blocker
        return result


def find_target_root(start: Path | None = None) -> Path:
    """Find a consumer or zutils source root without invoking Git."""
    current = (start or Path.cwd()).resolve()
    for root in (current, *current.parents):
        if (root / ".nanvix").is_dir():
            return root
        if (
            (root / "pyproject.toml").is_file()
            and (root / "templates").is_dir()
            and (root / "examples").is_dir()
        ):
            try:
                data = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                continue
            project = data.get("project")
            if (
                isinstance(project, dict)
                and cast("dict[str, object]", project).get("name") == "nanvix-zutil"
            ):
                return root
    raise UpdateError("could not find a Nanvix consumer or zutils source root")


def is_zutils_source(root: Path) -> bool:
    """Return whether *root* is a zutils source checkout."""
    return (root / "templates" / ".zutils-version").is_file() and (
        root / "examples"
    ).is_dir()


def load_sdk_target(value: str, root: Path) -> SdkRelease | None:
    """Parse an inline or local ``sdk-release.json`` target.

    A plain semantic version is intentionally not resolved over the network;
    update commands consume an already verified release contract and never
    access credentials.
    """
    candidate = Path(value)
    data: object
    if value.lstrip().startswith("{"):
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise UpdateError(f"--to contains malformed JSON: {exc}") from exc
    elif (
        candidate.is_absolute()
        or (root / candidate).exists()
        or "/" in value
        or "\\" in value
    ):
        path = candidate if candidate.is_absolute() else root / candidate
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise UpdateError(
                "SDK contract path must be an existing file under the target root"
            ) from exc
        try:
            data = json.loads(resolved.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UpdateError(f"cannot read SDK contract {resolved}: {exc}") from exc
    else:
        return None
    try:
        return validate_sdk_release(data)
    except ValueError as exc:
        raise UpdateError(str(exc)) from exc


def _safe_target(root: Path, relative: Path) -> Path:
    """Resolve a transaction destination without following a target symlink."""
    if relative.is_absolute() or ".." in relative.parts:
        raise UpdateError(f"unsafe update path: {relative}")
    raw_target = root / relative
    try:
        parent = raw_target.parent.resolve(strict=True)
        parent.relative_to(root)
    except (OSError, ValueError) as exc:
        raise UpdateError(f"update path escapes target root: {relative}") from exc
    target = parent / raw_target.name
    if target.is_symlink():
        raise UpdateError(f"update target is a symlink: {relative}")
    return target


def _fsync_directory(path: Path) -> None:
    """Flush directory metadata where the platform supports it."""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_file(path: Path) -> None:
    """Flush a file through a Windows-compatible read-write descriptor."""
    descriptor = os.open(path, os.O_RDWR)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_journal(journal: Path, data: dict[str, object]) -> None:
    """Crash-safely replace the transaction journal."""
    sidecar = journal.with_name(f".{journal.name}.{os.getpid()}.new")
    payload = (json.dumps(data, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(
        sidecar,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(sidecar, journal)
        _fsync_directory(journal.parent)
    finally:
        sidecar.unlink(missing_ok=True)


def _remove_journal(journal: Path, data: dict[str, object]) -> None:
    """Remove a journal durably, recreating it if directory fsync fails."""
    journal.unlink(missing_ok=True)
    try:
        _fsync_directory(journal.parent)
    except OSError:
        payload = (json.dumps(data, sort_keys=True) + "\n").encode("utf-8")
        journal.write_bytes(payload)
        journal.chmod(0o600)
        _fsync_file(journal)
        raise


def _restore_snapshot(
    target: Path,
    snapshot: bytes | None,
    mode: int,
) -> None:
    """Restore one snapshot using a mode-safe same-directory replacement."""
    if snapshot is None:
        target.unlink(missing_ok=True)
        _fsync_directory(target.parent)
        return
    sidecar = target.with_name(f".{target.name}.nanvix-zutil-rollback-{os.getpid()}")
    sidecar.write_bytes(snapshot)
    sidecar.chmod(mode)
    try:
        _fsync_file(sidecar)
        os.replace(sidecar, target)
        _fsync_directory(target.parent)
    finally:
        sidecar.unlink(missing_ok=True)


def _recover_journal(root: Path, journal: Path) -> None:
    """Recover an interrupted multi-file transaction while holding its lock."""
    if not journal.is_file():
        return
    try:
        raw: object = json.loads(journal.read_text("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("journal root is not an object")
        data = cast("dict[str, object]", raw)
        if data.get("state") == "committed":
            _remove_journal(journal, data)
            return
        if data.get("state") != "installing":
            raise ValueError("journal state is invalid")
        raw_files = data.get("files")
        if not isinstance(raw_files, list):
            raise ValueError("journal files are missing")
        entries: list[tuple[Path, bytes | None, int]] = []
        for item in cast("list[object]", raw_files):
            if not isinstance(item, dict):
                raise ValueError("journal file entry is invalid")
            entry = cast("dict[str, object]", item)
            relative_value = entry.get("path")
            mode = entry.get("mode")
            encoded = entry.get("content")
            if (
                not isinstance(relative_value, str)
                or not isinstance(mode, int)
                or isinstance(mode, bool)
            ):
                raise ValueError("journal file metadata is invalid")
            target = _safe_target(root, Path(relative_value))
            if encoded is None:
                snapshot = None
            elif isinstance(encoded, str):
                snapshot = base64.b64decode(encoded, validate=True)
            else:
                raise ValueError("journal content is invalid")
            entries.append((target, snapshot, mode))
        for target, snapshot, mode in entries:
            _restore_snapshot(target, snapshot, mode)
        _remove_journal(journal, data)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise UpdateError(f"cannot recover interrupted update: {exc}") from exc


def _windows_lock(descriptor: int) -> None:
    """Acquire a non-blocking one-byte Windows file lock."""
    import msvcrt

    if os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b"\0")
    os.lseek(descriptor, 0, os.SEEK_SET)
    locking = cast(
        "Callable[[int, int, int], None]",
        getattr(msvcrt, "locking"),
    )
    mode = cast(int, getattr(msvcrt, "LK_NBLCK"))
    try:
        locking(descriptor, mode, 1)
    except OSError as exc:
        raise BlockingIOError("update lock is held") from exc


def _windows_unlock(descriptor: int) -> None:
    """Release a one-byte Windows file lock."""
    import msvcrt

    os.lseek(descriptor, 0, os.SEEK_SET)
    locking = cast(
        "Callable[[int, int, int], None]",
        getattr(msvcrt, "locking"),
    )
    mode = cast(int, getattr(msvcrt, "LK_UNLCK"))
    locking(descriptor, mode, 1)


def _lock_descriptor(descriptor: int) -> None:
    """Acquire a non-destructive cross-platform OS file lock."""
    if os.name == "nt":
        _windows_lock(descriptor)
        return
    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise BlockingIOError("update lock is held") from exc


def _unlock_descriptor(descriptor: int) -> None:
    """Release a cross-platform OS file lock."""
    if os.name == "nt":
        _windows_unlock(descriptor)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _state_paths(root: Path) -> tuple[Path, Path]:
    """Return confined lock and journal paths for a target repository."""
    state_dir = root if is_zutils_source(root) else root / ".nanvix"
    if not state_dir.is_dir():
        raise UpdateError(f"update state directory does not exist: {state_dir}")
    return (
        state_dir / ".nanvix-zutil-update.lock",
        state_dir / ".nanvix-zutil-update-journal.json",
    )


@dataclass
class UpdateTransaction:
    """A locked, recovered repository update transaction."""

    root: Path
    journal: Path

    def install(
        self,
        candidates: dict[Path, bytes],
        validators: dict[Path, Callable[[bytes], None]],
        *,
        create_modes: dict[Path, int] | None = None,
    ) -> tuple[str, ...]:
        """Validate and atomically install candidate bytes."""
        return _atomic_write_candidates(
            self.root,
            candidates,
            validators,
            self.journal,
            create_modes=create_modes or {},
        )


@contextmanager
def update_transaction(root: Path) -> Generator[UpdateTransaction]:
    """Lock and recover a repository before any managed file is parsed."""
    resolved_root = root.resolve()
    lock, journal = _state_paths(resolved_root)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        try:
            _lock_descriptor(descriptor)
            locked = True
        except BlockingIOError as exc:
            raise UpdateError("another nanvix-zutil update is already running") from exc
        _recover_journal(resolved_root, journal)
        yield UpdateTransaction(resolved_root, journal)
    finally:
        try:
            if locked:
                _unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)


def atomic_write_candidates(
    root: Path,
    candidates: dict[Path, bytes],
    validators: dict[Path, Callable[[bytes], None]],
    *,
    create_modes: dict[Path, int] | None = None,
) -> tuple[str, ...]:
    """Recover, lock, and atomically install candidate bytes."""
    with update_transaction(root) as transaction:
        return transaction.install(
            candidates,
            validators,
            create_modes=create_modes,
        )


def _atomic_write_candidates(
    root: Path,
    candidates: dict[Path, bytes],
    validators: dict[Path, Callable[[bytes], None]],
    journal: Path,
    *,
    create_modes: dict[Path, int],
) -> tuple[str, ...]:
    """Install candidates while retaining a recoverable journal."""
    normalized: dict[Path, bytes] = {}
    snapshots: dict[Path, bytes | None] = {}
    modes: dict[Path, int] = {}
    targets: dict[Path, Path] = {}
    for relative, content in candidates.items():
        target = _safe_target(root, relative)
        if target.exists() and not target.is_file():
            raise UpdateError(f"target is not a regular file: {relative.as_posix()}")
        validator = validators.get(relative)
        if validator is not None:
            validator(content)
        normalized[relative] = content
        snapshots[relative] = target.read_bytes() if target.exists() else None
        modes[relative] = (
            stat.S_IMODE(target.stat().st_mode)
            if target.exists()
            else create_modes.get(relative, 0o644)
        )
        targets[relative] = target

    changed = tuple(
        sorted(
            relative.as_posix()
            for relative, content in normalized.items()
            if snapshots[relative] != content
        )
    )
    if not changed:
        return ()

    def encode_snapshot(relative: Path) -> str | None:
        snapshot = snapshots[relative]
        return (
            base64.b64encode(snapshot).decode("ascii") if snapshot is not None else None
        )

    journal_data: dict[str, object] = {
        "state": "installing",
        "files": [
            {
                "path": name,
                "mode": modes[Path(name)],
                "content": encode_snapshot(Path(name)),
            }
            for name in changed
        ],
    }
    _write_journal(journal, journal_data)
    sidecars: dict[Path, Path] = {}
    installed: list[Path] = []
    committed = False
    rollback_complete = False
    try:
        for name in changed:
            relative = Path(name)
            target = targets[relative]
            sidecar = target.with_name(f".{target.name}.nanvix-zutil-{os.getpid()}")
            sidecar.write_bytes(normalized[relative])
            sidecar.chmod(modes[relative])
            _fsync_file(sidecar)
            sidecars[relative] = sidecar
        for name in changed:
            relative = Path(name)
            target = targets[relative]
            os.replace(sidecars[relative], target)
            installed.append(relative)
            _fsync_directory(target.parent)
        journal_data["state"] = "committed"
        _write_journal(journal, journal_data)
        committed = True
    except OSError as exc:
        rollback_errors: list[str] = []
        for relative in reversed(installed):
            try:
                _restore_snapshot(
                    targets[relative],
                    snapshots[relative],
                    modes[relative],
                )
            except OSError as rollback_error:
                rollback_errors.append(f"{relative}: {rollback_error}")
        rollback_complete = not rollback_errors
        if rollback_errors:
            raise UpdateError(
                "atomic update failed and rollback is incomplete; journal"
                f" retained: {'; '.join(rollback_errors)}"
            ) from exc
        raise UpdateError(f"atomic update failed and was rolled back: {exc}") from exc
    finally:
        for sidecar in sidecars.values():
            sidecar.unlink(missing_ok=True)
        if committed or rollback_complete:
            _remove_journal(journal, journal_data)
    return changed


def emit_result(
    result: UpdateResult,
    *,
    output_format: str,
    output: Path | None,
) -> None:
    """Emit an update result to stdout or a confined output file."""
    if output_format == "json":
        rendered = json.dumps(result.to_dict(), sort_keys=True) + "\n"
    else:
        files = ", ".join(result.changed_files) if result.changed_files else "none"
        rendered = (
            f"{result.command}: {result.status}; target={result.target};"
            f" changed-files={files}\n"
        )
    if output is None or str(output) == "-":
        print(rendered, end="")
        return
    root = find_target_root()
    destination = output if output.is_absolute() else root / output
    try:
        destination = destination.resolve()
        destination.relative_to(root)
    except ValueError as exc:
        raise UpdateError("--output must stay within the target repository") from exc
    relative = destination.relative_to(root).as_posix()
    protected = {
        ".nanvix/nanvix.toml",
        ".nanvix/nanvix.lock",
        ".nanvix/z.py",
        ".nanvix/.gitignore",
        ".github/workflows/nanvix-ci.yml",
        ".zutils-version",
        "z",
        "z.sh",
        "z.ps1",
        "templates/.zutils-version",
        "templates/.gitignore",
        "templates/nanvix-ci.yml",
        "templates/z",
        "templates/z.sh",
        "templates/z.ps1",
        "examples/bin-hello/.nanvix/.gitignore",
        "examples/bin-hello/.nanvix/nanvix.lock",
        "examples/bin-hello/.nanvix/nanvix.toml",
        "examples/bin-hello/.nanvix/z.py",
        "examples/bin-hello/.zutils-version",
        "examples/bin-hello/z",
        "examples/bin-hello/z.sh",
        "examples/bin-hello/z.ps1",
        "examples/lib-hello/.nanvix/.gitignore",
        "examples/lib-hello/.nanvix/nanvix.lock",
        "examples/lib-hello/.nanvix/nanvix.toml",
        "examples/lib-hello/.nanvix/z.py",
        "examples/lib-hello/.zutils-version",
        "examples/lib-hello/z",
        "examples/lib-hello/z.sh",
        "examples/lib-hello/z.ps1",
    }
    if relative in protected:
        raise UpdateError("--output must not overwrite a managed update target")
    destination.parent.mkdir(parents=True, exist_ok=True)
    sidecar = destination.with_name(f".{destination.name}.nanvix-zutil-{os.getpid()}")
    try:
        sidecar.write_text(rendered, encoding="utf-8", newline="\n")
        os.replace(sidecar, destination)
    finally:
        sidecar.unlink(missing_ok=True)
