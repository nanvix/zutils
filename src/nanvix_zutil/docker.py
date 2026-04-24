# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Docker integration for nanvix_zutil consumer build scripts.

Provides per-command Docker wrapping so that build and test commands run
inside a Nanvix toolchain container without re-execing the whole script.
``setup()`` runs on the host (downloading sysroot/deps); ``build()``,
``test()``, etc. transparently wrap each :meth:`~nanvix_zutil.ZScript.run`
call in ``docker run``.

Typical usage (via :class:`~nanvix_zutil.ZScript` — not used directly)::

    ./z setup --with-docker
    ./z setup --with-docker nanvix/toolchain:v1.2.3
"""

from __future__ import annotations

import dataclasses
import os
import shlex
import shutil
import subprocess
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

# ---------------------------------------------------------------------------
# Well-known container paths
# ---------------------------------------------------------------------------

#: Container path for the consumer repository root.
WORKSPACE_CONTAINER_PATH: PurePosixPath = PurePosixPath("/mnt/workspace")

#: Container path for the Nanvix sysroot.
SYSROOT_CONTAINER_PATH: PurePosixPath = PurePosixPath("/mnt/sysroot")

#: Container path for the build-time dependency root (buildroot).
BUILDROOT_CONTAINER_PATH: PurePosixPath = PurePosixPath("/mnt/buildroot")

#: Container path for the Nanvix cross-compilation toolchain.
TOOLCHAIN_CONTAINER_PATH: PurePosixPath = PurePosixPath("/opt/nanvix")

#: Default Docker image used when ``--with-docker`` is passed
#: without an explicit image argument.
DEFAULT_DOCKER_IMAGE: str = "nanvix/toolchain:latest-minimal"


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------


def _get_uid() -> int:
    """Return the current user ID, or ``0`` on platforms without ``os.getuid``."""
    return os.getuid() if hasattr(os, "getuid") else 0


def _get_gid() -> int:
    """Return the current group ID, or ``0`` on platforms without ``os.getgid``."""
    return os.getgid() if hasattr(os, "getgid") else 0


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
    call then delegates to :meth:`build_run_cmd`, :meth:`build_kvm_run_cmd`
    (when ``kvm=True``), or :meth:`build_windows_run_cmd` (on Windows) to
    prepend the appropriate ``docker run`` invocation.

    Attributes:
        image: Docker image name (e.g. ``"nanvix/toolchain:latest-minimal"``).
        mounts: Ordered list of volume mounts.
        uid: User ID passed to ``--user``.  Defaults to the current process UID,
            or ``0`` on platforms where ``os.getuid`` is unavailable (e.g. Windows).
        gid: Group ID passed to ``--user``.  Defaults to the current process GID,
            or ``0`` on platforms where ``os.getgid`` is unavailable (e.g. Windows).
        workdir: Container working directory (used by both standard and KVM
            runs).  Defaults to :data:`WORKSPACE_CONTAINER_PATH`.
        extra_env: Additional ``-e KEY=VALUE`` pairs forwarded to the container.
    """

    image: str
    """Docker image name."""

    mounts: list[Mount] = field(default_factory=list)
    """Ordered list of volume mounts."""

    uid: int = field(default_factory=_get_uid)
    """User ID passed to ``--user`` (defaults to current process UID, or 0 on Windows)."""

    gid: int = field(default_factory=_get_gid)
    """Group ID passed to ``--user`` (defaults to current process GID, or 0 on Windows)."""

    workdir: PurePosixPath = field(default_factory=lambda: WORKSPACE_CONTAINER_PATH)
    """Container working directory (used by both standard and KVM runs)."""

    extra_env: dict[str, str] = field(default_factory=dict)
    """Additional ``-e KEY=VALUE`` pairs forwarded to the container."""

    output_files: list[str] = field(default_factory=list)
    """Build output files to copy back from the container to the host."""

    tar_excludes: list[str] = field(
        default_factory=lambda: [
            ".git",
            ".nanvix/venv",
            ".nanvix/cache",
            ".nanvix/sysroot",
            ".nanvix/buildroot",
        ]
    )
    """Directories/files to exclude from tar-based source copy."""

    container_build_dir: str = "/tmp/build"
    """Working directory inside the container for Windows tar-copy mode."""

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

    def with_sysroot_writable(self) -> DockerConfig:
        """Return a copy with the sysroot mount made read-write.

        Functional tests run ``nanvixd`` with ``cd /mnt/sysroot`` as the
        working directory.  ``nanvixd`` uses ``flexi_logger`` which writes
        log files into the CWD — this fails when the sysroot volume is
        mounted read-only.

        Returns:
            A new :class:`DockerConfig` with the sysroot mount (if any)
            changed to ``readonly=False``.  All other fields are shared.
        """
        new_mounts = [
            (
                dataclasses.replace(m, readonly=False)
                if m.container_path == SYSROOT_CONTAINER_PATH
                else m
            )
            for m in self.mounts
        ]
        return dataclasses.replace(self, mounts=new_mounts)

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

    def build_kvm_run_cmd(self, *cmd: str) -> list[str]:
        """Build a KVM-enabled ``docker run`` command list for functional tests.

        Differences from :meth:`build_run_cmd`:

        * Adds ``--device /dev/kvm`` and ``--group-add <kvm-gid>``.
        * Forces the sysroot mount to be writable (``nanvixd.elf`` needs
          write access); other mounts respect :attr:`Mount.readonly`.
        * Adds ``USER`` to the container environment.

        Args:
            *cmd: Inner command and arguments to wrap.

        Returns:
            Full ``docker run …`` argument list.
        """
        docker_cmd: list[str] = ["docker", "run", "--rm", "--device", "/dev/kvm"]
        if sys.platform != "linux":
            warnings.warn(
                "KVM is only available on Linux. The generated Docker command "
                "will include --device /dev/kvm which is not supported on "
                f"{sys.platform}.",
                stacklevel=2,
            )
        docker_cmd += ["--user", f"{self.uid}:{self.gid}"]

        kvm_gid = _get_kvm_gid()
        if kvm_gid:
            docker_cmd += ["--group-add", kvm_gid]

        for mount in self.mounts:
            # KVM is Linux-only (see warning above), so Windows-style
            # paths never appear here — no _translate_windows_path needed.
            # Sysroot must be writable for functional tests; other mounts
            # respect the readonly flag just like build_run_cmd().
            vol = f"{mount.host_path.resolve()}:{mount.container_path}"
            if mount.readonly and mount.container_path != SYSROOT_CONTAINER_PATH:
                vol += ":ro"
            docker_cmd += ["-v", vol]

        docker_cmd += ["-w", str(self.workdir)]
        docker_cmd += ["-e", "HOME=/tmp"]
        user = os.environ.get("USER") or os.environ.get("USERNAME") or "nanvix"
        docker_cmd += ["-e", f"USER={user}"]

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
        2. Copies sources via ``tar`` from the mounted workspace into a
           container-local build dir.
        3. Runs the inner command from the container-local build dir.
        4. Copies configured output files back to the mounted workspace.

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

        # Output copy-back script.
        output_script = ""
        if self.output_files:
            copy_cmds: list[str] = []
            for f in self.output_files:
                src = shlex.quote(f"{self.container_build_dir}/{f}")
                dst = shlex.quote(f"{WORKSPACE_CONTAINER_PATH}/{f}")
                dst_dir = shlex.quote(
                    str(PurePosixPath(f"{WORKSPACE_CONTAINER_PATH}/{f}").parent)
                )
                copy_cmds.append(
                    f"[ -f {src} ] && mkdir -p {dst_dir} && cp -f {src} {dst}"
                )
            output_script = "; " + "; ".join(copy_cmds)

        inner_cmd = " ".join(shlex.quote(c) for c in cmd)
        shell_script = (
            f"mkdir -p {build_dir} && "
            f"tar -cf - -C {ws_mount} {excludes} . "
            f"| tar -xf - -C {build_dir} && "
            f"cd {build_dir} && "
            f"{inner_cmd}; rc=$?{output_script}; exit $rc"
        )

        docker_cmd: list[str] = ["docker", "run", "--rm"]

        for mount in self.mounts:
            host = _translate_windows_path(mount.host_path.resolve())
            vol = f"{host}:{mount.container_path}"
            if mount.readonly:
                vol += ":ro"
            docker_cmd += ["-v", vol]

        docker_cmd += ["-w", self.container_build_dir]
        docker_cmd += ["-e", "HOME=/tmp"]

        for key, val in self.extra_env.items():
            docker_cmd += ["-e", f"{key}={val}"]

        docker_cmd.append(self.image)
        docker_cmd += ["sh", "-c", shell_script]
        return docker_cmd


# ---------------------------------------------------------------------------
# Availability helpers
# ---------------------------------------------------------------------------


def docker_available() -> bool:
    """Return ``True`` if the Docker CLI is present on ``PATH``.

    Returns:
        ``True`` when the ``docker`` executable can be found, ``False``
        otherwise.
    """
    return shutil.which("docker") is not None


def image_exists(image: str) -> bool:
    """Return ``True`` if *image* is available in the local Docker image cache.

    Args:
        image: Docker image reference (e.g.
            ``"nanvix/toolchain:latest-minimal"``).

    Returns:
        ``True`` when the image is present locally, ``False`` otherwise or
        when Docker is unavailable.
    """
    if not docker_available():
        return False
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_kvm_gid() -> str:
    """Return the GID of ``/dev/kvm`` as a string, or empty string on failure."""
    if sys.platform != "linux":
        return ""
    try:
        return str(os.stat("/dev/kvm").st_gid)
    except OSError:
        return ""
