# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Standalone-mode test-case abstraction for booting ELFs under ``nanvixd``.

Consumers that ship Nanvix ELF test binaries duplicate the same
boilerplate: locate ``nanvixd``/``mkramfs`` in the sysroot, build an
initrd, stage a ramfs, run ``mkramfs`` then ``nanvixd``, and translate
failures into a summary. This module hoists that plumbing behind
:class:`StandaloneTest` so each consumer's ``test()`` reduces to
building a list of cases and iterating.

Scope is deliberately narrow: **standalone deployment mode only**, one
ELF per case. Multi-/single-process modes require ``linuxd`` and are a
different plumbing story; upstream-driven test drivers (``make check``
& friends) stay outside this API. Strategy (allowlists, skiplists,
parametrised invocations, stdin fixtures) stays with the consumer.

Test failures raise :class:`StandaloneTestFailure`, a structured
exception carrying the case, exit code, and reason. Environment
failures (missing tools/ELF, broken sysroot) continue to surface as
``SystemExit`` — those are per-run problems, not per-test.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil.docker import is_windows
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP
from nanvix_zutil.helpers import InitRdArgs, make_initrd, mkramfs
from nanvix_zutil.paths import sysroot, test_out


@dataclass
class StandaloneTestFailure(Exception):
    """Raised by :meth:`StandaloneTest.run` when the run fails.

    Attributes:
        case: The failing test case (for callers who want to introspect
            more than the label).
        returncode: Child exit code. ``None`` when the run timed out
            or the ELF could not be executed.
        reason: Short human-readable description
            (``"exited 5"`` / ``"timed out after 120s"`` /
            ``"failed to execute: ..."``).
    """

    case: StandaloneTest
    returncode: int | None
    reason: str

    def __post_init__(self) -> None:
        # Keep Exception.args populated so pytest/log tooling that
        # reads .args still surfaces the failure detail.
        super().__init__(self.reason)

    def __str__(self) -> str:
        return f"{self.case.name}: {self.reason}"


@dataclass
class StandaloneTest:
    """One ELF-under-``nanvixd`` invocation in standalone mode.

    The ELF is read from :attr:`elf_path` as-is — no copy, no lookup
    in ``repo_root``. Point at wherever the build system drops it
    (typically ``repo_root() / "foo.elf"``).

    Attributes:
        elf_path: Location of the test ELF on disk.
        app_args: Argv appended to the app inside the initrd.
        nanvixd_args: Extra arguments spliced into the ``nanvixd``
            command line, immediately before the ``--`` separator
            (i.e. after ``-bin-dir``/``-ramfs`` and before the initrd
            path). Use this for per-platform toggles like
            ``-memory-size``.
        ramfs_files: Guest-path → host-path map staged into the ramfs
            before ``mkramfs`` runs. Guest paths are POSIX-style and
            relative to the ramfs root; parent directories are
            created as needed. A ``tmp/`` directory is always present.
        stdin: Bytes piped into ``nanvixd``'s standard input.
        timeout: Seconds before ``nanvixd`` is killed.
        label: Human-readable name for logs. Defaults to
            ``elf_path.stem``.
    """

    elf_path: Path
    app_args: Sequence[str] = ()
    nanvixd_args: Sequence[str] = ()
    ramfs_files: Mapping[str, Path] = field(default_factory=lambda: {})
    stdin: bytes | None = None
    timeout: int = 120
    label: str | None = None

    @property
    def name(self) -> str:
        return self.label or self.elf_path.stem

    @property
    def _fs_name(self) -> str:
        """``name`` sanitized for use in filesystem paths.

        Labels may contain ``/``, ``\\``, or other separators that
        would silently create subdirectories (or fail outright on
        Windows). Collapse anything outside ``[A-Za-z0-9._-]`` to ``_``.
        """
        return re.sub(r"[^A-Za-z0-9._-]+", "_", self.name) or "test"

    def run(self, script: object | None = None) -> None:
        """Boot the ELF under ``nanvixd``.

        Args:
            script: Accepted for API compatibility with callers that
                pass their :class:`ZScript`. Currently unused; the
                sysroot is resolved via :func:`nanvix_zutil.paths.sysroot`
                (which honors ``env.json``).

        Raises:
            StandaloneTestFailure: The child exited non-zero or timed
                out. Contains ``returncode`` and ``reason``.
            SystemExit: Environment failure (missing sysroot, ELF,
                ``nanvixd``, ``mkramfs``, or ``mkimage``). Not a test
                failure; propagate as-is.
        """
        name = self.name
        fs_name = self._fs_name
        sysroot_bin = sysroot() / "bin"
        nanvixd = sysroot_bin / ("nanvixd.exe" if is_windows() else "nanvixd.elf")
        if not nanvixd.is_file():
            log.fatal(
                f"{nanvixd.name} not found at {nanvixd}.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )

        if not self.elf_path.is_file():
            log.fatal(
                f"Test ELF not found: {self.elf_path}",
                code=EXIT_MISSING_DEP,
                hint="Build the test binaries before running tests.",
            )

        log.info(f"RUN  {name}")
        # Precompute so cleanup runs even if make_initrd raises (mkimage may
        # have written a partial image before failing).
        initrd = test_out() / f"{self._fs_name}.img"
        try:
            make_initrd(
                self.elf_path,
                test_out(),
                args=InitRdArgs(app_args=list(self.app_args) or None),
            )
            with tempfile.TemporaryDirectory(prefix=f"nanvix_{fs_name}_") as tmpdir:
                ramfs_img = Path(tmpdir) / f"rootfs_{fs_name}.img"
                mkramfs(ramfs_img, files=self.ramfs_files)
                self._invoke_nanvixd(nanvixd, sysroot_bin, ramfs_img, initrd)
        finally:
            if initrd.exists():
                initrd.unlink()

        log.success(f"OK   {name}")

    def _invoke_nanvixd(
        self,
        nanvixd: Path,
        sysroot_bin: Path,
        ramfs_img: Path,
        initrd: Path,
    ) -> None:
        """Run ``nanvixd`` and translate exit codes into typed failures.

        Bypasses :func:`nanvix_zutil.helpers.run` because that wrapper
        funnels non-zero exits through ``log.fatal`` and discards the
        child's returncode. We need the returncode intact so
        :class:`StandaloneTestFailure` can carry it.
        """
        cmd = [
            str(nanvixd),
            "-bin-dir",
            str(sysroot_bin),
            "-ramfs",
            str(ramfs_img),
            *self.nanvixd_args,
            "--",
            str(initrd),
        ]
        log.info(f"$ {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                input=self.stdin,
                text=(self.stdin is None),
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            log.warning(f"FAIL {self.name} (timed out after {self.timeout}s)")
            raise StandaloneTestFailure(
                case=self,
                returncode=None,
                reason=f"timed out after {self.timeout}s",
            ) from exc
        except OSError as exc:
            # Non-executable ELF, permission-denied, etc. The nanvixd
            # tool exists (checked up front) so this is about the
            # subprocess failing to start with the given argv.
            log.warning(f"FAIL {self.name} (failed to execute: {exc})")
            raise StandaloneTestFailure(
                case=self,
                returncode=None,
                reason=f"failed to execute: {exc}",
            ) from exc
        if result.returncode != 0:
            log.warning(f"FAIL {self.name} (exited {result.returncode})")
            raise StandaloneTestFailure(
                case=self,
                returncode=result.returncode,
                reason=f"exited {result.returncode}",
            )
