# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Base class for Nanvix consumer build scripts.

Consumer repositories subclass :class:`ZScript`, implement the lifecycle
hooks they need, and call ``ZScript.main()`` as the entry point::

    from nanvix_zutil import ZScript

    class MyBuild(ZScript):
        def build(self) -> None:
            self.run("make", "-f", "Makefile.nanvix", "all")

    if __name__ == "__main__":
        MyBuild.main()

Invoke via the ``nanvix-zutil`` CLI::

    nanvix-zutil setup
    nanvix-zutil build
    nanvix-zutil test
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

from nanvix_zutil import EXIT_BUILD_FAILURE, log
from nanvix_zutil.buildroot import (
    Buildroot,
    Dependency,
    RefKind,
    extract_nanvix_version,
    extract_nanvix_version_base,
    suffix_dep,
)
from nanvix_zutil.cli import build_parser
from nanvix_zutil.config import CFG_DOCKER_IMAGE, CFG_GH_TOKEN, CFG_SYSROOT, Config
from nanvix_zutil.docker import (
    BUILDROOT_CONTAINER_PATH,
    SYSROOT_CONTAINER_PATH,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
    is_windows,
)
from nanvix_zutil.exitcodes import (
    EXIT_DEGRADED_SETUP,
    EXIT_INVALID_ARGS,
    EXIT_MISSING_DEP,
)
from nanvix_zutil.github import resolve_release, resolve_release_with_fallback
from nanvix_zutil.helpers import (
    check_docker,
    filter_container_env,
    sync_configs,
)
from nanvix_zutil.lockfile import get_zutil_version, read_lockfile, write_lockfile
from nanvix_zutil.manifest import Manifest, load_manifest
from nanvix_zutil.paths import (
    buildroot as _buildroot_dir,
)
from nanvix_zutil.paths import (
    nanvix_root,
    out_dir,
    repo_root,
)
from nanvix_zutil.paths import (
    sysroot as _sysroot_dir,
)
from nanvix_zutil.resolver import is_stale, resolve
from nanvix_zutil.sysroot import Sysroot


class ZScript:
    """Base class for consumer build scripts.

    Provides CLI dispatch, config management, subprocess execution, and
    structured logging.  Consumers subclass this and implement the lifecycle
    hooks they need.

    The :meth:`setup` and :meth:`lock` hooks are *auto-implemented* in
    the base class and are always available in the CLI.
    The remaining hooks (``build``, ``test``, ``benchmark``, ``release``,
    ``clean``) only appear in the help menu when the subclass overrides them.

    Attributes:
        SYSROOT_REQUIRED_FILES: Files that must exist in the sysroot
            regardless of deployment mode.  Override in subclasses to add
            library-specific files.
        SYSROOT_MULTI_PROCESS_FILES: Additional files required only for
            multi-process deployments (``linuxd.elf``, ``uservm.elf``).
        config: Persistent build configuration loaded from
            ``.nanvix/env.json`` and environment variables.
        log: The :mod:`nanvix_zutil.log` module, accessible as ``self.log``
            for structured logging in lifecycle hooks.
        targets: Arguments passed after ``--`` on the command line.
            Lifecycle hooks can use these to customise behavior
            (e.g. ``nanvix-zutil test -- smoke integration``).
        sysroot: The :class:`~nanvix_zutil.Sysroot` downloaded by
            :meth:`setup`, or ``None`` before setup runs.
        buildroot: The :class:`~nanvix_zutil.Buildroot` populated by
            :meth:`setup`, or ``None`` when there are no dependencies.
        docker: Active :class:`~nanvix_zutil.DockerConfig`, or ``None``
            when Docker mode is not in use.
    """

    SYSROOT_REQUIRED_FILES: tuple[str, ...] = (
        "lib/libposix.a",
        "lib/user.ld",
        "bin/nanvixd.elf",
        "bin/kernel.elf",
        "bin/mkramfs.elf",
    )

    SYSROOT_REQUIRED_FILES_WINDOWS: tuple[str, ...] = (
        "lib/libposix.a",
        "lib/user.ld",
        "bin/nanvixd.exe",
        "bin/kernel.elf",
        "bin/mkramfs.exe",
    )

    SYSROOT_MULTI_PROCESS_FILES: tuple[str, ...] = (
        "bin/linuxd.elf",
        "bin/uservm.elf",
    )

    SYSROOT_STANDALONE_FILES: tuple[str, ...] = (
        "bin/mkimage.elf",
        "bin/procd.elf",
        "bin/memd.elf",
        "bin/vfsd.elf",
    )

    SYSROOT_STANDALONE_FILES_WINDOWS: tuple[str, ...] = (
        "bin/mkimage.exe",
        "bin/procd.elf",
        "bin/memd.elf",
        "bin/vfsd.elf",
    )

    #: Hooks that are auto-implemented in the base class and always
    #: available in the CLI, regardless of subclass overrides.
    AUTO_HOOKS: tuple[str, ...] = (
        "setup",
        "lock",
        "install",
        "help",
    )

    #: Consumer-defined hooks that appear in the CLI only when the
    #: subclass overrides the corresponding method.
    CONSUMER_HOOKS: tuple[str, ...] = (
        "build",
        "test",
        "benchmark",
        "release",
        "clean",
    )

    # Subcommands that always run inside Docker.
    DOCKER_COMMANDS: frozenset[str | None] = frozenset(
        {"setup", "build", "release", "clean"}
    )

    def sysroot_required_files(self) -> list[str]:
        """Return the sysroot files required for the current platform and mode.

        Uses Windows binary names (``nanvixd.exe``, ``mkramfs.exe``) on
        Windows; Linux names on other platforms.  Multi-process mode
        additionally requires ``linuxd.elf`` and ``uservm.elf``.
        Standalone mode additionally requires ``mkimage``, ``procd.elf``,
        ``memd.elf``, and ``vfsd.elf``.
        Subclasses can extend by overriding the class attributes or
        this method.
        """
        if is_windows():
            files = list(self.SYSROOT_REQUIRED_FILES_WINDOWS)
        else:
            files = list(self.SYSROOT_REQUIRED_FILES)
        if self.config.deployment_mode == "multi-process":
            files.extend(self.SYSROOT_MULTI_PROCESS_FILES)
        if self.config.deployment_mode == "standalone":
            if is_windows():
                files.extend(self.SYSROOT_STANDALONE_FILES_WINDOWS)
            else:
                files.extend(self.SYSROOT_STANDALONE_FILES)
        return files

    def __init__(self) -> None:
        """Initialise ZScript for *repo_root*."""
        self.config = Config(nanvix_root())
        self.log = log
        self.targets: list[str] = []
        self.manifest: Manifest = load_manifest()
        self.sysroot: Sysroot | None = None
        self.buildroot: Buildroot | None = None
        self.docker: DockerConfig | None = None
        self._used_fallback: bool = False
        self._offline: bool = False
        self._with_nanvix_path: str | None = os.environ.get("WITH_NANVIX")
        self._cli_sysroot_path: str | None = None

    # ------------------------------------------------------------------
    # Hook classification helpers
    # ------------------------------------------------------------------

    def available_subcommands(self) -> tuple[str, ...]:
        """Return the subcommands to register in the CLI parser.

        :attr:`AUTO_HOOKS` are always included.  :attr:`CONSUMER_HOOKS`
        are included only when the concrete subclass overrides the
        corresponding method.

        Returns:
            Ordered tuple of subcommand names.
        """
        available: list[str] = list(self.AUTO_HOOKS)
        for name in self.CONSUMER_HOOKS:
            if getattr(type(self), name) is not getattr(ZScript, name):
                available.append(name)
        return tuple(available)

    # ------------------------------------------------------------------
    # Docker hooks — override in subclass to customise
    # ------------------------------------------------------------------

    def docker_config(self, image: str) -> DockerConfig:
        """Build the :class:`~nanvix_zutil.DockerConfig` for *image*.

        Constructs a standard configuration that mounts:

        * :attr:`repo_root` → ``/mnt/workspace`` (writable, workdir)
        * sysroot path from :attr:`config` → ``/mnt/sysroot`` (read-only),
          if the sysroot has been configured
        * ``.nanvix/buildroot`` → ``/mnt/buildroot`` (writable),
          if the buildroot directory exists on disk

        Override in a subclass to add extra mounts or environment variables.

        Args:
            image: Docker image name to use.

        Returns:
            A fully populated :class:`~nanvix_zutil.DockerConfig`.
        """
        mounts: list[Mount] = [
            Mount(
                host_path=repo_root(),
                container_path=WORKSPACE_CONTAINER_PATH,
                readonly=False,
            ),
        ]

        sysroot_str = self.config.get(CFG_SYSROOT)
        if sysroot_str:
            mounts.append(
                Mount(
                    host_path=Path(sysroot_str),
                    container_path=SYSROOT_CONTAINER_PATH,
                    readonly=True,
                )
            )

        if _buildroot_dir().is_dir():
            mounts.append(
                Mount(
                    host_path=_buildroot_dir(),
                    container_path=BUILDROOT_CONTAINER_PATH,
                    readonly=False,
                )
            )

        return DockerConfig(
            image=image,
            mounts=mounts,
            workdir=WORKSPACE_CONTAINER_PATH,
        )

    # ------------------------------------------------------------------
    # Path translation helper
    # ------------------------------------------------------------------

    def translate_path(self, host_path: Path) -> PurePosixPath | Path:
        """Translate *host_path* to its container equivalent if Docker is active.

        When Docker mode is not active, returns *host_path* unchanged.

        Args:
            host_path: An absolute host-side path.

        Returns:
            Container-side :class:`~pathlib.PurePosixPath` when Docker is active,
            otherwise *host_path*.
        """
        log.warning(
            "DEPRECATED: Use self.docker.translate_path instead of self.translate_path."
        )
        if self.docker is not None:
            return self.docker.translate_path(host_path)
        return host_path

    # ------------------------------------------------------------------
    # Lifecycle hooks — auto-implemented
    # ------------------------------------------------------------------

    def setup(self) -> bool:
        """Prepare the build environment.

        The base implementation automatically downloads the Nanvix sysroot
        and all build-time dependencies declared in ``nanvix.toml``, then
        saves the resulting paths to :attr:`config`.

        Subclasses may override this to perform additional setup steps.
        Call ``super().setup()`` to retain the automatic download behaviour::

            def setup(self) -> bool:
                failed = super().setup()
                # extra verification or configuration here
                return failed

        Returns:
            ``True`` if any dependency was resolved via version fallback, or any
            other failure condition
            ``False`` if all dependencies matched their exact requested
            versions.
        """
        self._used_fallback = False

        # Resolve sysroot: --sysroot-path / NANVIX_VERSION (path) take precedence.
        # NANVIX_VERSION is only treated as a path when it is absolute AND
        # points to an existing directory — this avoids accidentally matching
        # a version string like "3" against a relative directory.
        _env_version = os.environ.get("NANVIX_VERSION")
        sysroot_path = self._cli_sysroot_path or (
            _env_version
            if _env_version
            and Path(_env_version).is_absolute()
            and Path(_env_version).is_dir()
            else None
        )

        if sysroot_path:
            self.sysroot = Sysroot.from_local(
                Path(sysroot_path),
                config=self.config,
            )
        elif self.manifest.sysroot_ref.kind == RefKind.LOCAL:
            self.sysroot = Sysroot.from_local(
                Path(str(self.manifest.sysroot_ref.value)),
                config=self.config,
            )
        elif self._offline:
            log.fatal(
                "Offline mode requires a local sysroot."
                " Set --sysroot-path or NANVIX_VERSION to a directory.",
                code=EXIT_MISSING_DEP,
            )
        else:
            self.sysroot = Sysroot.download(
                machine=self.config.machine,
                deployment_mode=self.config.deployment_mode,
                memory_size=self.config.memory_size,
                tag=self.manifest.sysroot_ref.value,
                gh_token=self.config.get(CFG_GH_TOKEN),
                dest=_sysroot_dir(),
                config=self.config,
            )
        self.config.set(CFG_SYSROOT, str(self.sysroot.path))

        # On Windows, download host-native binaries (nanvixd.exe, mkramfs.exe)
        # BEFORE verifying required files — the base sysroot from
        # Sysroot.download() only has Linux .elf binaries.
        if is_windows():
            self.sysroot.download_windows_binaries(
                machine=self.config.machine,
                deployment_mode=self.config.deployment_mode,
                memory_size=self.config.memory_size,
                gh_token=self.config.get(CFG_GH_TOKEN),
                config=self.config,
            )

        # When --with-nanvix PATH is passed, overlay local build artifacts
        # (nanvixd.elf, mkramfs.elf, uservm.elf, libposix.a, etc.) on top
        # of the downloaded sysroot before verification.
        # Re-read WITH_NANVIX here in case the env var was set after __init__
        # (e.g., by a parent orchestrator between construction and setup()).
        nanvix_local = self._with_nanvix_path or os.environ.get("WITH_NANVIX")
        if self._offline and not nanvix_local:
            log.fatal(
                "Offline mode requires --with-nanvix or WITH_NANVIX to"
                " provide local artifacts.",
                code=EXIT_MISSING_DEP,
            )
        if nanvix_local:
            self.sysroot.overlay_local_nanvix(Path(nanvix_local))

        self.sysroot.verify(self.sysroot_required_files())

        # Deferred auto-suffix: when sysroot is "latest", load_manifest()
        # skips suffixing because the real version isn't known yet.  Now
        # that the sysroot is resolved, suffix VERSION deps before
        # passing them to install_dep().
        deps: list[Dependency] = list(self.manifest.dependencies)
        if self.manifest.sysroot_ref.value == "latest":
            if self.sysroot.tag:
                resolved_version = self.sysroot.tag.removeprefix("v")
                deps = [suffix_dep(d, resolved_version) for d in deps]
            elif not self._offline:
                log.fatal(
                    "Sysroot resolved to 'latest' but no tag is available"
                    " — delete .nanvix/sysroot and re-run 'nanvix-zutil setup'.",
                    code=EXIT_MISSING_DEP,
                )
            # In offline mode with no tag, deps remain un-suffixed.
            # This is acceptable because offline resolution uses local
            # paths (deps/<name>/) which are version-agnostic.

        if deps:
            self.buildroot = Buildroot.create()
            for dep in deps:
                # LOCAL deps: install from the filesystem path directly.
                if dep.ref.kind == RefKind.LOCAL:
                    local_dep_path = Path(str(dep.ref.value))
                    if not self.buildroot.install_local_nanvix(dep, local_dep_path):
                        log.fatal(
                            f"Local dependency path has no artifacts for"
                            f" '{dep.name}': {local_dep_path}",
                            code=EXIT_MISSING_DEP,
                            hint=f"Ensure '{local_dep_path}/deps/{dep.name}/' "
                            "contains lib/ and/or include/ directories.",
                        )
                    continue

                # When --with-nanvix is active, try local artifacts first.
                # In offline mode, try for ALL deps (not just nanvix-owned).
                # In online mode, only try for nanvix-owned deps.
                if nanvix_local:
                    should_try_local = self._offline or dep.repo.startswith("nanvix/")
                    if should_try_local and self.buildroot.install_local_nanvix(
                        dep, Path(nanvix_local)
                    ):
                        continue

                # In offline mode, warn if local artifacts were not found.
                # nanvix_local is guaranteed set here (fatal above).
                if self._offline:
                    log.warning(
                        f"Offline mode: no local artifacts found for '{dep.name}'."
                        f" Expected at: {nanvix_local}/deps/{dep.name}/",
                    )
                    continue

                try:
                    # Resolve release with version fallback for nanvix-suffixed
                    # deps, then pass the pre-resolved release and enable
                    # cross-mode asset fallback in install_dep().
                    release: dict[str, object] | None = None
                    base_version = extract_nanvix_version_base(str(dep.ref.value))
                    if base_version is not None:
                        release, fb_ver = resolve_release_with_fallback(
                            repo=dep.repo,
                            version_specifier=str(dep.ref.value),
                            base_version=base_version,
                            gh_token=self.config.get(CFG_GH_TOKEN),
                        )
                        # Log when version fallback was used.
                        # fb_ver is None when the exact tag was found;
                        # non-None means a fallback release was used.
                        if fb_ver is not None:
                            self._used_fallback = True
                            requested_ver = extract_nanvix_version(str(dep.ref.value))
                            log.warning(
                                f"Version fallback for {dep.name}: "
                                f"requested nanvix-{requested_ver}, "
                                f"resolved nanvix-{fb_ver}"
                            )
                    else:
                        release = resolve_release(
                            repo=dep.repo,
                            version_specifier=dep.ref.value,
                            gh_token=self.config.get(CFG_GH_TOKEN),
                        )

                    self.buildroot.install_dep(
                        dep=dep,
                        machine=self.config.machine,
                        deployment_mode=self.config.deployment_mode,
                        memory_size=self.config.memory_size,
                        gh_token=self.config.get(CFG_GH_TOKEN),
                        _release=release,
                    )
                except SystemExit:
                    log.note(
                        f"while installing dependency '{dep.name}'"
                        f" ({dep.repo}@{dep.ref.value})"
                    )
                    raise

        self.config.save()
        sync_configs()
        return self._used_fallback

    def install_artifacts(self, output: str) -> None:
        """Export build artifacts to a target directory.

        Copies the port's output ``.a`` libraries, headers, and binaries
        into ``<output>/{lib,include,bin}/`` from ``.nanvix/output/``.

        Note:
            This intentionally writes outside ``.nanvix/`` — it is the
            only subcommand that does so.

        Args:
            output: Absolute path to the target directory.
        """
        output_path = Path(output)
        output_path.mkdir(parents=True, exist_ok=True)

        # Source: .nanvix/out/{lib,include,bin}/
        for subdir in ("lib", "include", "bin"):
            src = out_dir() / subdir
            if src.is_dir():
                dst = output_path / subdir
                dst.mkdir(parents=True, exist_ok=True)
                for item in src.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(src)
                        dst_file = dst / rel
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, dst_file)

        log.info(f"Artifacts exported to {output_path}")

    def lock(self, *, shallow: bool = False) -> None:
        """Resolve the dependency graph and write ``nanvix.lock``.

        Args:
            shallow: When ``True``, resolve only direct dependencies
                (skip transitive discovery).
        """
        lockfile = resolve(
            self.manifest,
            gh_token=self.config.get(CFG_GH_TOKEN),
            shallow=shallow,
        )
        lock_path = nanvix_root() / "nanvix.lock"
        write_lockfile(lockfile, lock_path)
        log.success(f"Wrote {lock_path}")

    def lock_check(self) -> None:
        """Verify that ``nanvix.lock`` is up-to-date.

        Exits with ``EXIT_MISSING_DEP`` if the lockfile does not exist, or
        ``EXIT_INVALID_ARGS`` if it is stale relative to ``nanvix.toml``.
        """
        lock_path = nanvix_root() / "nanvix.lock"
        lockfile = read_lockfile(lock_path)

        # Warn when "latest" lockfile may silently be stale.
        sysroot_pkg = next((p for p in lockfile.packages if p.name == "nanvix"), None)
        if sysroot_pkg is not None and sysroot_pkg.ref.value == "latest":
            log.warning(
                "'nanvix-version = \"latest\"' — lockfile staleness cannot"
                " be detected by hash; re-run 'nanvix-zutil lock' to pick up new"
                " releases."
            )

        if is_stale(lockfile):
            log.fatal(
                "Lockfile is stale — nanvix.toml has changed since it was generated.",
                code=EXIT_INVALID_ARGS,
                hint="Run `nanvix-zutil lock` to regenerate the lockfile.",
            )

    # ------------------------------------------------------------------
    # Lifecycle hooks — override in subclass
    # ------------------------------------------------------------------

    def build(self) -> None:
        """Build the project.

        Override to invoke the project's build system.
        """

    def test(self) -> None:
        """Run the project's test suite.

        Override to invoke the project's tests.
        """

    def benchmark(self) -> None:
        """Run the project's benchmarks.

        Override to invoke the project's benchmarks.
        """

    def release(self) -> None:
        """Package a release artifact.

        Override to produce the project's distribution archive.
        """

    def clean(self) -> None:
        """Remove build artifacts.

        On Windows, common build artifacts are removed directly without
        invoking the build system (which would require Docker).  Override
        to customise the files cleaned.
        """
        if is_windows():
            # Common artifacts that consumers may produce.
            # Subclasses can override to add project-specific files.
            for name in (".nanvix-configured",):
                p = repo_root() / name
                if p.is_file():
                    p.unlink()
                    log.info(f"Removed {name}")

    # ------------------------------------------------------------------
    # Image helpers
    # ------------------------------------------------------------------

    def make_initrd(
        self,
        app: str,
        *,
        app_args: list[str] | None = None,
        app_env: list[str] | None = None,
        procd_args: list[str] | None = None,
        procd_env: list[str] | None = None,
        memd_args: list[str] | None = None,
        memd_env: list[str] | None = None,
        vfsd_args: list[str] | None = None,
        vfsd_env: list[str] | None = None,
        kernel_args: list[str] | None = None,
        bin_dir: Path | None = None,
    ) -> Path:
        """Build a standalone initrd image for *app*.

        Invokes ``mkimage`` from the sysroot ``bin/`` directory to produce
        an image containing the system daemons (``procd``, ``memd``,
        ``vfsd``) and the application binary.

        Each program entry in the image has the format
        ``<path>;<argv0> [arg1 arg2 ...][;KEY=VAL ...]``.  An unescaped
        ``;`` separates CLI arguments from environment variables (see
        ``run-linux.md`` for the full protocol).

        Args:
            app: A bare application binary filename (e.g. ``"my-app.elf"``).
                Must include the extension and must not contain path
                separators (``/`` or ``\\``).
            app_args: Optional CLI arguments appended to the app's argv.
            app_env: Optional environment variables for the app as
                ``KEY=VALUE`` strings.
            procd_args: Optional CLI arguments appended to ``procd``'s argv.
            procd_env: Optional environment variables for ``procd`` as
                ``KEY=VALUE`` strings.
            memd_args: Optional CLI arguments appended to ``memd``'s argv.
            memd_env: Optional environment variables for ``memd`` as
                ``KEY=VALUE`` strings.
            vfsd_args: Optional CLI arguments appended to ``vfsd``'s argv.
            vfsd_env: Optional environment variables for ``vfsd`` as
                ``KEY=VALUE`` strings.
            kernel_args: Optional arguments passed to the kernel via
                ``-kernel-args``.
            bin_dir: Directory containing the ELF binaries and
                ``mkimage``.  Defaults to the sysroot ``bin/`` directory.

        Returns:
            Path to the generated ``.img`` file.

        Raises:
            SystemExit: If *app* contains path separators, the sysroot
                is unavailable, or ``mkimage`` is missing.
        """
        log.warning("DEPRECATED: Use helpers.make_initrd instead of self.make_initrd.")
        # Validate that app is a bare filename — no directory components.
        if "/" in app or "\\" in app:
            log.fatal(
                f"app must be a bare filename without path separators, got: {app!r}",
                code=EXIT_MISSING_DEP,
            )

        if bin_dir is None:
            if self.sysroot is not None:
                bin_dir = self.sysroot.path / "bin"
            else:
                sysroot_str = self.config.get(CFG_SYSROOT)
                if sysroot_str:
                    bin_dir = Path(sysroot_str) / "bin"
                else:
                    log.fatal(
                        "Sysroot not available; run setup first.",
                        code=EXIT_MISSING_DEP,
                    )

        if is_windows():
            mkimage = bin_dir / "mkimage.exe"
        else:
            mkimage = bin_dir / "mkimage.elf"

        # Verify mkimage exists before attempting to run it.
        if not mkimage.exists():
            log.fatal(
                f"mkimage not found at {mkimage}; run setup first.",
                code=EXIT_MISSING_DEP,
                hint="Ensure the sysroot contains mkimage by running ./z setup.",
            )

        app_stem = Path(app).stem
        output = repo_root() / f"{app_stem}.img"

        def _escape(arg: str) -> str:
            return arg.replace(";", "\\;")

        def _entry(
            elf: str | Path,
            argv0: str,
            extra: list[str] | None,
            env: list[str] | None = None,
        ) -> str:
            parts = [_escape(argv0)] + [_escape(a) for a in (extra or [])]
            argv = " ".join(parts)
            if env:
                env_str = " ".join(_escape(e) for e in env)
                return f"{_escape(str(elf))};{argv};{env_str}"
            return f"{_escape(str(elf))};{argv}"

        cmd: list[str] = [
            str(mkimage),
            "-o",
            str(output),
        ]

        if kernel_args:
            escaped = [_escape(a) for a in kernel_args]
            cmd.extend(["-kernel-args", " ".join(escaped)])

        cmd.extend(
            [
                _entry(bin_dir / "procd.elf", "procd", procd_args, procd_env),
                _entry(bin_dir / "memd.elf", "memd", memd_args, memd_env),
                _entry(bin_dir / "vfsd.elf", "vfsd", vfsd_args, vfsd_env),
                _entry(repo_root() / app, app, app_args, app_env),
            ]
        )

        self.run(*cmd, docker=False)

        return output

    # ------------------------------------------------------------------
    # Subprocess helper
    # ------------------------------------------------------------------

    def run(
        self,
        *args: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        docker: bool = True,
        timeout: int | None = None,
    ) -> "subprocess.CompletedProcess[str]":
        """Run a subprocess, logging the command before execution.

        When Docker mode is active (i.e. ``--with-docker`` was passed
        during ``setup`` and the current subcommand auto-loads it),
        the command is transparently wrapped in ``docker run``.

        Args:
            *args: Command and arguments to execute.
            cwd: Working directory for the subprocess.  Defaults to
                :attr:`repo_root`.  Ignored when Docker wrapping is active
                (the container workdir is controlled by
                :class:`~nanvix_zutil.DockerConfig`).
            env: Environment variables for the subprocess.  ``None`` inherits
                the current process environment.  When Docker mode is active,
                these variables are forwarded into the container via ``-e``
                flags rather than being applied to the Docker client process.
            docker: When ``False``, always runs on the host even if Docker
                mode is active.  Use this for commands that must run locally
                (e.g. ``clean``).
            timeout: Maximum seconds to wait for the process to finish.
                ``None`` means wait indefinitely.  When the timeout expires
                the process tree is killed and a fatal error is raised.

        Returns:
            The completed process result.

        Raises:
            SystemExit: With exit code ``5`` if the process exits with a
                non-zero status or the timeout expires.
        """
        log.warning("DEPRECATED: Use helpers.run instead of self.run.")
        if self.docker is not None and docker:
            cfg = self.docker
            if env:
                # Strip host-only / shell-managed keys (e.g. Windows' ``PATH``)
                # before forwarding into the container.  See
                # :func:`helpers.filter_container_env` for the rationale.
                merged = {**cfg.extra_env, **filter_container_env(env)}
                cfg = dataclasses.replace(cfg, extra_env=merged)
            if is_windows():
                cmd = cfg.build_windows_run_cmd(*args)
            else:
                cmd = cfg.build_run_cmd(*args)
            working_dir = repo_root()
            subprocess_env: dict[str, str] | None = None
        else:
            cmd = list(args)
            working_dir = cwd if cwd is not None else repo_root()
            if env is not None:
                subprocess_env = env
            else:
                subprocess_env = None

        log.info(f"$ {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                cwd=working_dir,
                env=subprocess_env,
                text=True,
                check=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            log.fatal(
                f"Command timed out after {timeout}s: {' '.join(args)}",
                code=EXIT_BUILD_FAILURE,
            )
        except subprocess.CalledProcessError as exc:
            log.fatal(
                f"Command failed with exit code {exc.returncode}: {' '.join(args)}",
                code=EXIT_BUILD_FAILURE,
            )
        return result

    # ------------------------------------------------------------------
    # CLI entry point
    # ------------------------------------------------------------------

    @classmethod
    def main(cls) -> None:
        """Parse command-line arguments and dispatch to the appropriate
        lifecycle hook.

        When *repo_root* is ``None`` (the default — direct invocation via
        ``python .nanvix/z.py``), the repository root is inferred from
        ``sys.argv[0]``.  When provided (by the ``nanvix-zutil`` CLI),
        the inference is skipped entirely.

        Args:
            repo_root: Optional explicit path to the consumer repository
                root.  When ``None``, the root is inferred from the
                location of the calling script.
        """
        argv = sys.argv[1:]

        # Split sys.argv on '--' to separate framework args from targets.
        if "--" in argv:
            sep = argv.index("--")
            framework_argv = argv[:sep]
            targets = argv[sep + 1 :]
        else:
            framework_argv = argv
            targets = []

        # Pre-parse --json and --version before creating the instance so
        # that JSON mode is active for any errors raised during __init__,
        # and --version can exit cleanly without requiring a valid manifest.
        # Also pre-parse --mode so that --mode can override
        # NANVIX_DEPLOYMENT_MODE before Config.__init__ runs.
        pre_parser = argparse.ArgumentParser(add_help=False)
        pre_parser.add_argument("--json", action="store_true", default=False)
        pre_parser.add_argument(
            "--version",
            action="version",
            version=f"%(prog)s (nanvix-zutil {get_zutil_version()})",
        )
        pre_parser.add_argument("--mode", default=None, dest="mode")
        pre_args, _ = pre_parser.parse_known_args(framework_argv)

        if pre_args.json:
            log.set_json_mode(True)

        # Detect --help/-h and the 'help' subcommand (or no subcommand at
        # all) BEFORE loading the manifest.  A missing nanvix.toml must not
        # prevent the user from reading help text.
        positional_args = [a for a in framework_argv if not a.startswith("-")]
        first_positional = positional_args[0] if positional_args else None
        help_requested = (
            "-h" in framework_argv
            or "--help" in framework_argv
            or first_positional in (None, "help")
        )
        if help_requested:
            # Build a full static parser (all subcommands) for help display.
            build_parser().parse_args(framework_argv)  # --help/-h → sys.exit(0)
            build_parser().print_help()  # 'help' subcommand or no args
            return

        # ------------------------------------------------------------------
        # Handle --mode: override NANVIX_DEPLOYMENT_MODE before Config
        # __init__ reads it during instance construction.
        # ------------------------------------------------------------------
        cli_mode = getattr(pre_args, "mode", None)
        if cli_mode is not None:
            os.environ["NANVIX_DEPLOYMENT_MODE"] = cli_mode

        instance = cls()
        instance.targets = targets

        # Build the parser dynamically: auto hooks are always registered;
        # consumer hooks only appear when the subclass overrides them.
        parser = build_parser(available=instance.available_subcommands())
        args = parser.parse_args(framework_argv)

        # ------------------------------------------------------------------
        # Handle --offline, --with-nanvix, --sysroot-path from CLI.
        # ------------------------------------------------------------------
        if getattr(args, "offline", False):
            instance._offline = True
        if getattr(args, "with_nanvix", None):
            instance._with_nanvix_path = args.with_nanvix
        if getattr(args, "sysroot_path", None):
            instance._cli_sysroot_path = args.sysroot_path

        # ------------------------------------------------------------------
        # Docker: resolve image from CLI or persisted config, then check availability.
        # ------------------------------------------------------------------

        # On Windows, setup downloads host-native binaries
        # via the GitHub API and does not invoke Docker.  Skip Docker
        # entirely for setup on Windows — the image is still persisted
        # to .nanvix/env.json so that subsequent commands can use it,
        # but we do not require Docker to be installed or the image to
        # exist locally.
        if args.subcommand in ZScript.DOCKER_COMMANDS:
            image: str | None = getattr(args, "with_docker", None)
            persisted_image = instance.config.get(CFG_DOCKER_IMAGE)

            if image is None:
                if not persisted_image:
                    log.fatal(
                        "No Docker image configured — run 'setup --with-docker IMAGE' first.",
                        code=EXIT_INVALID_ARGS,
                    )
                image = persisted_image

            # TODO: Move into setup()
            # https://github.com/nanvix/zutils/issues/187
            # https://github.com/nanvix/zutils/issues/190
            if args.subcommand == "setup":
                if persisted_image is not None and persisted_image != image:
                    log.warning(
                        f"Overriding previously configured Docker image '{persisted_image}'"
                        f" with '{image}'."
                    )
                instance.config.set(CFG_DOCKER_IMAGE, image)
                instance.config.save()

            if not is_windows():
                # may exit if Docker is required but not available
                check_docker(image)

            # Persist Docker image on setup so subsequent commands
            # automatically use the same image.
            instance.docker = instance.docker_config(image)

        # ------------------------------------------------------------------
        # Dispatch to lifecycle hook
        # ------------------------------------------------------------------
        subcommand: str | None = args.subcommand

        # Special handling for lock subcommand (--check, --shallow flags).
        if subcommand == "lock":
            if args.check:
                instance.lock_check()
                log.success("Lockfile is up-to-date")
            else:
                instance.lock(shallow=args.shallow)
            return

        # Special handling for install subcommand (--output flag).
        if subcommand == "install":
            instance.install_artifacts(output=args.output)
            log.success("Install complete")
            return

        dispatch: dict[str, object] = {
            "setup": instance.setup,
            "build": instance.build,
            "test": instance.test,
            "benchmark": instance.benchmark,
            "release": instance.release,
            "clean": instance.clean,
        }

        handler = dispatch.get(subcommand) if subcommand is not None else None
        if callable(handler) and subcommand is not None:
            handler_result = handler()

            if subcommand == "setup":
                if instance._used_fallback:
                    log.fatal(
                        f"{subcommand.capitalize()} complete with fallback dependencies",
                        code=EXIT_DEGRADED_SETUP,
                    )
                # NOTE: This is semantically backwards,
                # but we're going to be making setup standalone soon.
                # Leave it so we don't have to modify downstreams.
                elif handler_result is True:
                    log.fatal(
                        "Setup override returned True, indicating failure.",
                        code=EXIT_DEGRADED_SETUP,
                    )
            log.success(f"{subcommand.capitalize()} complete")
        else:
            log.fatal(f"Unknown subcommand: {subcommand}", code=EXIT_INVALID_ARGS)
