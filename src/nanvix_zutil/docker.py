# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Docker integration for nanvix_zutil consumer build scripts.

Provides per-command Docker wrapping so that build and test commands run
inside a Nanvix toolchain container without re-execing the whole script.
Docker mode is always enabled for ``setup``, ``build``, ``release``, and
``clean`` — if Docker or the required image is unavailable the command
fails immediately.  ``test`` and ``benchmark`` run natively on the host.

Use ``--with-docker IMAGE`` during setup to specify the Docker image::

    ./z setup --with-docker ghcr.io/nanvix/nanvix-sdk-c-clang@sha256:<digest>
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

# ---------------------------------------------------------------------------
# Well-known container paths
# ---------------------------------------------------------------------------

#: Container path for the consumer repository root.
WORKSPACE_CONTAINER_PATH: PurePosixPath = PurePosixPath("/mnt/workspace")

#: Container path for the Nanvix sysroot.
SYSROOT_CONTAINER_PATH: PurePosixPath = PurePosixPath("/mnt/sysroot")

#: Container path for the Nanvix cross-compilation toolchain.
TOOLCHAIN_CONTAINER_PATH: PurePosixPath = PurePosixPath("/opt/nanvix")

# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------


def _get_uid() -> int:
    """Return the current user ID, or ``0`` on platforms without ``os.getuid``."""
    return (
        os.getuid()  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType,reportUnknownVariableType]
        if hasattr(os, "getuid")
        else 0
    )


def _get_gid() -> int:
    """Return the current group ID, or ``0`` on platforms without ``os.getgid``."""
    return (
        os.getgid()  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType,reportUnknownVariableType]
        if hasattr(os, "getgid")
        else 0
    )


def is_windows() -> bool:
    """Return ``True`` when running on Windows.

    Extracted to a named function so tests can mock it without
    patching ``sys.platform`` globally.
    """
    return sys.platform == "win32"


def _translate_windows_path(p: Path) -> str:
    """Convert a Windows absolute path to Docker-compatible POSIX format.

    ``C:\\Users\\foo`` → ``/c/Users/foo``

    Uses :class:`~pathlib.PureWindowsPath` to parse the drive letter and
    normalise separators, then rewrites drive-letter paths into the
    MSYS-style ``/<drive>/…`` prefix that Docker Desktop expects for
    ``-v`` volume mounts.  Paths without a drive letter are returned as
    POSIX (forward-slash) strings unchanged.

    .. note::

       This function does **not** check ``sys.platform``.  Platform
       gating is the caller's responsibility — :meth:`build_run_cmd`
       only calls this when :func:`is_windows` is ``True``, while
       :meth:`build_windows_run_cmd` calls it unconditionally (it is
       only invoked on Windows in the first place).
    """
    wp = PureWindowsPath(p)
    posix = wp.as_posix()
    if wp.drive:
        # 'C:/Users/foo' → '/c/Users/foo'
        return f"/{wp.drive[0].lower()}{posix[2:]}"
    return posix


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Mount:
    """A host→container volume mount for a Docker container.

    Attributes:
        host_path: Absolute host-side path to mount.
        container_path: Mount point inside the container.
        readonly: When ``True`` the volume is mounted with ``:ro``.
    """

    host_path: Path
    """Absolute host-side path to mount."""

    container_path: PurePosixPath
    """Mount point inside the container."""

    readonly: bool = False
    """When ``True`` the volume is mounted read-only (``:ro``)."""


@dataclass
class DockerConfig:
    """Configuration for per-command Docker wrapping.

    An instance is stored on :class:`~nanvix_zutil.ZScript` once a Docker
    flag is passed on the command line.  Each :meth:`~nanvix_zutil.ZScript.run`
    call then delegates to :meth:`build_run_cmd` or
    :meth:`build_windows_run_cmd` (on Windows) to prepend the appropriate
    ``docker run`` invocation.

    Attributes:
        image: Immutable Docker image reference (for example,
            ``"ghcr.io/nanvix/nanvix-sdk-c-clang@sha256:<digest>"``).
        mounts: Ordered list of volume mounts.
        uid: User ID passed to ``--user``.  Defaults to the current process UID,
            or ``0`` on platforms where ``os.getuid`` is unavailable (e.g. Windows).
        gid: Group ID passed to ``--user``.  Defaults to the current process GID,
            or ``0`` on platforms where ``os.getgid`` is unavailable (e.g. Windows).
        workdir: Container working directory.  Defaults to
            :data:`WORKSPACE_CONTAINER_PATH`.
        extra_env: Additional ``-e KEY=VALUE`` pairs forwarded to the container.
    """

    image: str
    """Docker image name."""

    mounts: list[Mount] = field(default_factory=lambda: [])
    """Ordered list of volume mounts."""

    uid: int = field(default_factory=_get_uid)
    """User ID passed to ``--user`` (defaults to current process UID, or 0 on Windows)."""

    gid: int = field(default_factory=_get_gid)
    """Group ID passed to ``--user`` (defaults to current process GID, or 0 on Windows)."""

    workdir: PurePosixPath = field(default_factory=lambda: WORKSPACE_CONTAINER_PATH)
    """Container working directory."""

    extra_env: dict[str, str] = field(default_factory=lambda: {})
    """Additional ``-e KEY=VALUE`` pairs forwarded to the container."""

    output_files: list[str] = field(default_factory=lambda: [])
    """Build outputs to copy back from the container to the workspace.

    Each entry is a path relative to *both* the build dir and the workspace
    mount -- they mirror, so the source and destination share the same
    relative path.  Directories are wiped and copied with ``cp -a`` (``cp -aL``
    on a Windows host, to dereference symlinks); files are copied with ``cp``.
    File-vs-directory is detected at runtime in the container.  Each path is
    validated lexically to stay within the workspace (it must not resolve to
    or above the workspace root).
    """

    crlf_files: list[str] = field(default_factory=lambda: [])
    """Files (relative to the build dir) to normalize from CRLF to LF after sync."""

    tar_excludes: list[str] = field(
        default_factory=lambda: [
            ".git",
            ".nanvix/venv",
            ".nanvix/cache",
            ".nanvix/sysroot",
        ]
    )
    """Directories/files to exclude from tar-based source copy."""

    container_build_dir: str = "/tmp/build"
    """Working directory inside the container for Windows tar-copy mode."""

    persistent_volume: bool | str = False
    """Persist the build dir in a named Docker volume across ``run`` calls.

    When ``True`` a deterministic volume name is derived from the workspace
    (``<workspace-name>-build-<md5[:8]>``); a string is used verbatim as the
    volume name.  The volume is mounted at :attr:`container_build_dir` so the
    source sync becomes incremental instead of re-copying every call.  Only
    affects the Windows tar-copy path (:meth:`build_windows_run_cmd`).
    """

    # ------------------------------------------------------------------
    # Path translation
    # ------------------------------------------------------------------

    def translate_path(self, host_path: Path) -> PurePosixPath:
        """Translate a host path to its container equivalent.

        Scans :attr:`mounts` and returns the container-side path for the
        longest matching host prefix.  If no mount covers *host_path*, the
        path is returned as a :class:`PurePosixPath` so container-internal
        paths keep forward slashes on Windows.

        Args:
            host_path: An absolute host path to translate.

        Returns:
            Container-side :class:`~pathlib.PurePosixPath`.  When no mount
            covers the path, the result is still a :class:`PurePosixPath`
            so that container-internal paths (e.g. ``/opt/nanvix``) keep
            forward slashes on Windows.
        """
        resolved = host_path.resolve()
        best_mount: Mount | None = None
        best_depth = -1
        best_rel = Path(".")

        for mount in self.mounts:
            mount_host = mount.host_path.resolve()
            try:
                rel = resolved.relative_to(mount_host)
            except ValueError:
                continue
            depth = len(mount_host.parts)
            if depth > best_depth:
                best_depth = depth
                best_mount = mount
                best_rel = rel

        if best_mount is not None:
            return best_mount.container_path / PurePosixPath(*best_rel.parts)
        # No mount matched — return as PurePosixPath so container-internal
        # paths like /opt/nanvix keep forward slashes on Windows.
        return PurePosixPath(host_path.as_posix())

    # ------------------------------------------------------------------
    # Mount helpers
    # ------------------------------------------------------------------

    def _workspace_host_path(self) -> Path:
        """Return the host path mounted at the workspace, or raise."""
        for mount in self.mounts:
            if mount.container_path == WORKSPACE_CONTAINER_PATH:
                return mount.host_path
        raise ValueError("no workspace mount configured")

    def volume_name(self) -> str | None:
        """Return the persistent build volume name, or ``None`` when disabled.

        A string :attr:`persistent_volume` is used verbatim.  ``True`` derives
        a deterministic name from the workspace host path:
        ``<workspace-name>-build-<md5[:8]>``.
        """
        if not self.persistent_volume:
            return None
        if isinstance(self.persistent_volume, str):
            return self.persistent_volume
        ws = self._workspace_host_path().resolve()
        digest = hashlib.md5(ws.as_posix().encode(), usedforsecurity=False).hexdigest()[
            :8
        ]
        return f"{ws.name}-build-{digest}"

    # ------------------------------------------------------------------
    # Command construction
    # ------------------------------------------------------------------

    def build_run_cmd(self, *cmd: str) -> list[str]:
        """Build a standard ``docker run`` command list.

        The resulting command mounts all volumes as configured and sets
        ``--workdir`` to :attr:`workdir`.

        Args:
            *cmd: Inner command and arguments to wrap.

        Returns:
            Full ``docker run …`` argument list ready for
            :func:`subprocess.run`.
        """
        docker_cmd: list[str] = ["docker", "run", "--rm"]

        docker_cmd += ["--user", f"{self.uid}:{self.gid}"]

        for mount in self.mounts:
            host = (
                _translate_windows_path(mount.host_path.resolve())
                if is_windows()
                else str(mount.host_path.resolve())
            )
            vol = f"{host}:{mount.container_path}"
            if mount.readonly:
                vol += ":ro"
            docker_cmd += ["-v", vol]

        docker_cmd += ["-w", str(self.workdir)]
        docker_cmd += ["-e", "HOME=/tmp"]

        for key, val in self.extra_env.items():
            docker_cmd += ["-e", f"{key}={val}"]

        docker_cmd.append(self.image)
        docker_cmd.extend(cmd)
        return docker_cmd

    def build_windows_run_cmd(self, *cmd: str) -> list[str]:
        """Build a ``docker run`` command using tar-based source copying.

        Instead of building directly from the mounted workspace (which suffers
        severe I/O penalties on Windows Docker Desktop via VirtioFS/9p), this
        method:

        1. Uses the configured container mounts as provided, typically including
           the host workspace at ``/mnt/workspace``.
        2. Copies sources from the mounted workspace into a container-local
           build dir, preferring ``rsync`` (preserves mtimes; keeps a
           persistent build dir incremental) and falling back to ``tar``
           when rsync is unavailable.
        3. Runs the inner command from the container-local build dir.
        4. Copies configured ``output_files`` back to the mounted workspace
           (directories as trees, files individually).
           Directories are wiped and copied with ``cp -a``; files are
           copied with ``cp``.

        Args:
            *cmd: Inner command and arguments to wrap.

        Returns:
            Full ``docker run …`` argument list ready for
            :func:`subprocess.run`.
        """
        ws_mount = shlex.quote(str(WORKSPACE_CONTAINER_PATH))
        build_dir = shlex.quote(self.container_build_dir)

        # Build tar exclude args.
        excludes = " ".join(f"--exclude={shlex.quote(e)}" for e in self.tar_excludes)

        # Copy build outputs back to the mounted workspace. Each entry mirrors
        # the same relative path in the build dir and the workspace mount.
        # Directories are wiped and copied with cp -a; files are copied with
        # cp. Joined with `;` so a missing output does not abort the others.
        #
        # The container runs as root (no --user), so on a real bind mount the
        # copied-back outputs are chown'd back to uid:gid. This is skipped on a
        # Windows host: there is no os.getuid() (uid/gid default to 0), so
        # chowning would force root:root and lock the files away from the
        # Windows user -- Docker Desktop maps ownership itself. Mirrors the old
        # _restore_owner guard.
        output_script = ""
        copy_cmds: list[str] = []
        ws = str(WORKSPACE_CONTAINER_PATH)
        windows_host = is_windows()
        restore_owner = not windows_host
        # On a Windows host, dereference symlinks when copying dir trees so the
        # bind mount receives real files. Docker Desktop's WSL2 backend renders
        # container symlinks as LX reparse points that native Windows tools
        # cannot follow or unlink (WinError 1920). On Linux, preserve symlinks.
        cp_dir = "cp -aL" if windows_host else "cp -a"
        for f in self.output_files:
            # Guard the path: entries are relative to the workspace/build dir
            # (they mirror), and dir outputs are wiped with ``rm -rf``. Reject
            # absolute paths and any value (empty, ``.``, ``..``-escaping) that
            # resolves to or above the workspace root.
            if posixpath.isabs(f):
                raise ValueError(f"output path {f!r} must be relative to the workspace")
            dst_norm = posixpath.normpath(f"{ws}/{f}")
            if dst_norm == ws or not dst_norm.startswith(f"{ws}/"):
                raise ValueError(
                    f"output path {f!r} must be a non-empty path "
                    "within the workspace"
                )
            src = shlex.quote(f"{self.container_build_dir}/{f}")
            dst = shlex.quote(f"{WORKSPACE_CONTAINER_PATH}/{f}")
            dst_dir = shlex.quote(
                str(PurePosixPath(f"{WORKSPACE_CONTAINER_PATH}/{f}").parent)
            )
            # Restore ownership of the copied-back output (and its contents
            # for a dir tree) to the host user.
            chown_dir = (
                f"chown -R {self.uid}:{self.gid} {dst} 2>/dev/null || true; "
                if restore_owner
                else ""
            )
            chown_file = (
                f"chown {self.uid}:{self.gid} {dst} 2>/dev/null || true; "
                if restore_owner
                else ""
            )
            copy_cmds.append(
                f"if [ -d {src} ]; then rm -rf {dst} && mkdir -p {dst} "
                f"&& {cp_dir} {src}/. {dst}/; {chown_dir}"
                f"elif [ -f {src} ]; then mkdir -p {dst_dir} && cp -f {src} {dst}; "
                f"{chown_file}fi"
            )
        if copy_cmds:
            output_script = "; " + "; ".join(copy_cmds)

        # Prefer rsync (preserves mtimes, syncs only changed files so a
        # persistent build dir stays incremental); fall back to tar when rsync
        # is not present in the image. No --delete: it would wipe build
        # artifacts and the .build-inputs-hash from a persistent volume. The
        # excludes string is shared: rsync and tar interpret --exclude=
        # slightly differently (rsync anchors leading-/ patterns), so keep
        # excludes to basename patterns to stay branch-agnostic.
        rsync_cmd = f"rsync -a {excludes} {ws_mount}/ {build_dir}/"
        tar_cmd = f"tar -cf - -C {ws_mount} {excludes} . | tar -xf - -C {build_dir}"
        sync_cmd = (
            f"if command -v rsync >/dev/null 2>&1; then {rsync_cmd}; "
            f"else {tar_cmd}; fi"
        )

        # Normalize CRLF -> LF on caller-supplied files after sync. Avoids
        # autotools/shell-script breakage on Windows checkouts without
        # core.autocrlf=input. Uses tr (POSIX \r escape); the normalized
        # content is written back with `cat tmp > file` so the file's mode is
        # preserved (a plain `mv` would drop the execute bit off configure).
        crlf_cmd = ""
        if self.crlf_files:
            norms: list[str] = []
            for f in self.crlf_files:
                path = shlex.quote(f"{self.container_build_dir}/{f}")
                tmp = shlex.quote(f"{self.container_build_dir}/{f}.crlf.tmp")
                norms.append(
                    f"if [ -f {path} ]; then "
                    f"tr -d '\\r' < {path} > {tmp} && cat {tmp} > {path} "
                    f"&& rm -f {tmp}; fi"
                )
            crlf_cmd = " && ".join(norms) + " && "

        inner_cmd = " ".join(shlex.quote(c) for c in cmd)
        shell_script = (
            f"mkdir -p {build_dir} && "
            f"{sync_cmd} && "
            f"cd {build_dir} && "
            f"{crlf_cmd}"
            f"{inner_cmd}; rc=$?{output_script}; exit $rc"
        )

        docker_cmd: list[str] = ["docker", "run", "--rm"]

        for mount in self.mounts:
            host = _translate_windows_path(mount.host_path.resolve())
            vol = f"{host}:{mount.container_path}"
            if mount.readonly:
                vol += ":ro"
            docker_cmd += ["-v", vol]

        # Persist the build dir in a named volume so the source sync is
        # incremental across calls instead of a fresh tmpdir each time.
        vol_name = self.volume_name()
        if vol_name:
            docker_cmd += ["-v", f"{vol_name}:{self.container_build_dir}"]

        docker_cmd += ["-w", self.container_build_dir]
        docker_cmd += ["-e", "HOME=/tmp"]

        for key, val in self.extra_env.items():
            docker_cmd += ["-e", f"{key}={val}"]

        docker_cmd.append(self.image)
        docker_cmd += ["sh", "-c", shell_script]
        return docker_cmd


# ---------------------------------------------------------------------------
# Volume lifecycle
# ---------------------------------------------------------------------------


def remove_build_volume(name: str) -> None:
    """Remove a persistent Docker build volume, if Docker is available.

    A no-op when the ``docker`` CLI is missing.  Uses ``--force`` so a
    non-existent volume is not an error.
    """
    if shutil.which("docker") is None:
        return
    subprocess.run(["docker", "volume", "rm", "--force", name], check=False)
