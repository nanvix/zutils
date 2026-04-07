# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Docker integration for nanvix_zutil consumer build scripts.

Provides per-command Docker wrapping so that build and test commands run
inside a Nanvix toolchain container without re-execing the whole script.
``setup()`` runs on the host (downloading sysroot/deps); ``build()``,
``test()``, etc. transparently wrap each :meth:`~nanvix_zutil.ZScript.run`
call in ``docker run``.

Typical usage (via :class:`~nanvix_zutil.ZScript` — not used directly)::

    ./z build --with-docker
    ./z test  --with-minimal-docker
    ./z build --docker-image nanvix/toolchain:v1.2.3
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Well-known container paths
# ---------------------------------------------------------------------------

#: Container path for the consumer repository root.
WORKSPACE_CONTAINER_PATH: Path = Path("/mnt/workspace")

#: Container path for the Nanvix sysroot.
SYSROOT_CONTAINER_PATH: Path = Path("/mnt/sysroot")

#: Container path for the build-time dependency root (buildroot).
BUILDROOT_CONTAINER_PATH: Path = Path("/mnt/buildroot")

#: Container path for the Nanvix cross-compilation toolchain.
TOOLCHAIN_CONTAINER_PATH: Path = Path("/opt/nanvix")

#: Default Docker image used when ``--with-docker`` or
#: ``--with-minimal-docker`` is requested.
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

    container_path: Path
    """Mount point inside the container."""

    readonly: bool = False
    """When ``True`` the volume is mounted read-only (``:ro``)."""


@dataclass
class DockerConfig:
    """Configuration for per-command Docker wrapping.

    An instance is stored on :class:`~nanvix_zutil.ZScript` once a Docker
    flag is passed on the command line.  Each :meth:`~nanvix_zutil.ZScript.run`
    call then delegates to :meth:`build_run_cmd` (or :meth:`build_kvm_run_cmd`
    when ``kvm=True``) to prepend the appropriate ``docker run`` invocation.

    Attributes:
        image: Docker image name (e.g. ``"nanvix/toolchain:latest-minimal"``).
        mounts: Ordered list of volume mounts.
        uid: User ID passed to ``--user``.  Defaults to the current process UID,
            or ``0`` on platforms where ``os.getuid`` is unavailable (e.g. Windows).
        gid: Group ID passed to ``--user``.  Defaults to the current process GID,
            or ``0`` on platforms where ``os.getgid`` is unavailable (e.g. Windows).
        workdir: Container working directory for standard (non-KVM) runs.
            Defaults to :data:`WORKSPACE_CONTAINER_PATH`.
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

    workdir: Path = field(default_factory=lambda: WORKSPACE_CONTAINER_PATH)
    """Container working directory for standard runs."""

    extra_env: dict[str, str] = field(default_factory=dict)
    """Additional ``-e KEY=VALUE`` pairs forwarded to the container."""

    # ------------------------------------------------------------------
    # Path translation
    # ------------------------------------------------------------------

    def translate_path(self, host_path: Path) -> Path:
        """Translate a host path to its container equivalent.

        Scans :attr:`mounts` and returns the container-side path for the
        longest matching host prefix.  If no mount covers *host_path*, the
        original path is returned unchanged.

        Args:
            host_path: An absolute host path to translate.

        Returns:
            Container-side :class:`~pathlib.Path`, or *host_path* if no
            mount matches.
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
            return best_mount.container_path / best_rel
        return host_path

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
            vol = f"{mount.host_path.resolve()}:{mount.container_path}"
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
        * Sets ``--workdir`` to :data:`SYSROOT_CONTAINER_PATH` so that
          ``nanvixd.elf`` can resolve relative paths correctly.
        * Mounts the sysroot volume as writable (``nanvixd.elf`` needs write
          access).
        * Adds ``USER`` to the container environment.

        Args:
            *cmd: Inner command and arguments to wrap.

        Returns:
            Full ``docker run …`` argument list.
        """
        docker_cmd: list[str] = ["docker", "run", "--rm", "--device", "/dev/kvm"]
        docker_cmd += ["--user", f"{self.uid}:{self.gid}"]

        kvm_gid = _get_kvm_gid()
        if kvm_gid:
            docker_cmd += ["--group-add", kvm_gid]

        for mount in self.mounts:
            # Sysroot must be writable for functional tests.
            vol = f"{mount.host_path.resolve()}:{mount.container_path}"
            docker_cmd += ["-v", vol]

        docker_cmd += ["-w", str(SYSROOT_CONTAINER_PATH)]
        docker_cmd += ["-e", "HOME=/tmp"]
        docker_cmd += ["-e", f"USER={os.environ.get('USER', 'nanvix')}"]

        for key, val in self.extra_env.items():
            docker_cmd += ["-e", f"{key}={val}"]

        docker_cmd.append(self.image)
        docker_cmd.extend(cmd)
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
    try:
        return str(os.stat("/dev/kvm").st_gid)
    except OSError:
        return ""
