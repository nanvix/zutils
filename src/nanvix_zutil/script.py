# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Base class for Nanvix consumer build scripts.

Consumer repositories subclass :class:`ZScript`, implement the lifecycle
hooks they need, and call ``ZScript.main()`` as the entry point::

    from nanvix_zutil import ZScript
    from nanvix_zutil.helpers import run

    class MyBuild(ZScript):
        def build(self) -> None:
            run("make", "-f", "Makefile.nanvix", "all", docker=self.docker)

    if __name__ == "__main__":
        MyBuild.main()

Invoke via the ``nanvix-zutil`` CLI::

    nanvix-zutil setup
    nanvix-zutil build
    nanvix-zutil test
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

from nanvix_zutil import log
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
    sync_configs,
)
from nanvix_zutil.lockfile import get_zutil_version, read_lockfile, write_lockfile
from nanvix_zutil.manifest import Manifest, load_manifest
from nanvix_zutil.paths import buildroot as _buildroot_dir
from nanvix_zutil.paths import dist_dir, nanvix_root, out_dir, release_dir, repo_root
from nanvix_zutil.paths import sysroot as _sysroot_dir
from nanvix_zutil.release import package
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
        "release",
    )

    #: Consumer-defined hooks that appear in the CLI only when the
    #: subclass overrides the corresponding method.
    CONSUMER_HOOKS: tuple[str, ...] = (
        "build",
        "test",
        "benchmark",
        "clean",
    )

    # Subcommands that always run inside Docker.
    DOCKER_COMMANDS: frozenset[str | None] = frozenset(
        {"setup", "build", "release", "clean"}
    )

    def release_targets(self) -> dict[str, str]:
        """
        Consumer-provided release targets.
        By default, `./z release` will wrap everything in the `release_dir()`.
        Specify this value to override.
        This value maps from subdirectory names to release artifacts.

        Example usage:
        ```python
        def release_targets() -> dict[str,str]:
            return {
                # release_dir()/sysroot-pkg -> dist_dir()/{name}-{toolchain}.tar.gz
                "sysroot-pkg": f"{name}-{toolchain}",
                # release_dir()/buildroot-pkg -> dist_dir()/{name}-{toolchain}.tar.gz
                "buildroot-pkg": f"{name}-{toolchain}-buildroot",
            }
        ```
        """
        return {}

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
        self.config = Config()
        self.log = log
        self.targets: list[str] = []
        self.manifest: Manifest = load_manifest()
        self.sysroot: Sysroot | None = None
        self.buildroot: Buildroot | None = None
        self.docker: DockerConfig | None = None
        self._used_fallback: bool = False
        self._offline: bool = False
        self._with_nanvix_path: str | None = None
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

        * :func:`repo_root()` → ``/mnt/workspace`` (writable, workdir)
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

        # Resolve sysroot: --sysroot-path takes precedence.
        sysroot_path = self._cli_sysroot_path

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
                " Set --sysroot-path to a directory.",
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
        nanvix_local = self._with_nanvix_path
        if self._offline and not nanvix_local:
            log.fatal(
                "Offline mode requires --with-nanvix to" " provide local artifacts.",
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

    def release(self) -> None:
        """Package release archives from ``.nanvix/out/release``.

        The resulting archives are written to ``.nanvix/out/dist`` under the
        manifest package name.
        """
        manifest = load_manifest()

        def check_target(target: str):
            allowlist = re.compile(r"^[A-Za-z0-9_.-]+$")
            if not allowlist.match(target) and target not in (".", ".."):
                log.fatal(
                    f"Invalid release target '{target}'."
                    "Characters must be alphanumeric, underscore, hyphen, or dot.",
                    code=EXIT_INVALID_ARGS,
                )

        if self.release_targets() == {}:
            name = (
                f"{manifest.name}"
                f"-{self.config.machine}"
                f"-{self.config.deployment_mode}"
                f"-{self.config.memory_size}"
            )
            package([release_dir()], dist_dir(), name)
        else:
            for input, output in self.release_targets().items():
                check_target(input)
                check_target(output)
                package([release_dir() / input], dist_dir(), output)

    def install_artifacts(self, output: str) -> None:
        """Export build artifacts to a target directory.

        Copies the port's output ``.a`` libraries, headers, and binaries
        into ``<output>/{lib,include,bin}/`` from ``.nanvix/out/``.

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
    # CLI entry point
    # ------------------------------------------------------------------

    @classmethod
    def main(cls) -> None:
        """Parse command-line arguments and dispatch to the appropriate
        lifecycle hook."""
        argv = sys.argv[1:]

        # Split sys.argv on '--' to separate framework args from targets.
        if "--" in argv:
            sep = argv.index("--")
            framework_argv = argv[:sep]
            targets = argv[sep + 1 :]
        else:
            framework_argv = argv
            targets = []

        # Pre-parse --version and --mode before creating the instance so
        # that --version can exit cleanly without requiring a valid manifest,
        # and so that --mode can override NANVIX_DEPLOYMENT_MODE before
        # Config.__init__ runs.
        pre_parser = argparse.ArgumentParser(add_help=False)
        pre_parser.add_argument(
            "--version",
            action="version",
            version=f"%(prog)s (nanvix-zutil {get_zutil_version()})",
        )
        pre_parser.add_argument("--mode", default=None, dest="mode")
        pre_args, _ = pre_parser.parse_known_args(framework_argv)

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
