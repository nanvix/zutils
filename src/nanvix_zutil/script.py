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

from nanvix_zutil import log
from nanvix_zutil.buildroot import (
    Buildroot,
    Dependency,
    extract_nanvix_version,
    extract_nanvix_version_base,
    suffix_dep,
)
from nanvix_zutil.cli import build_parser
from nanvix_zutil.config import CFG_GH_TOKEN, CFG_SYSROOT, Config
from nanvix_zutil.docker import (
    BUILDROOT_CONTAINER_PATH,
    DEFAULT_DOCKER_IMAGE,
    SYSROOT_CONTAINER_PATH,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
    docker_available,
    image_exists,
    is_windows,
)
from nanvix_zutil.exitcodes import (
    EXIT_BUILD_FAILURE,
    EXIT_INVALID_ARGS,
    EXIT_MISSING_DEP,
    EXIT_TEST_FAILURE,
)
from nanvix_zutil.github import resolve_release, resolve_release_with_fallback
from nanvix_zutil.lockfile import get_zutil_version, read_lockfile, write_lockfile
from nanvix_zutil.manifest import Manifest, load_manifest
from nanvix_zutil.matrix import (
    expand_matrix,
    filter_matrix,
    print_summary,
    run_all_builds,
)
from nanvix_zutil.resolver import is_stale, resolve
from nanvix_zutil.sysroot import Sysroot


class ZScript:
    """Base class for consumer build scripts.

    Provides CLI dispatch, config management, subprocess execution, and
    structured logging.  Consumers subclass this and implement the lifecycle
    hooks they need.

    The :meth:`setup`, :meth:`distclean`, and :meth:`lock` hooks are
    *auto-implemented* in the base class and are always available in the CLI.
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
        repo_root: Absolute path to the consumer repository root.
        nanvix_dir: Absolute path to the ``.nanvix/`` directory.
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

    SYSROOT_MULTI_PROCESS_FILES: tuple[str, ...] = (
        "bin/linuxd.elf",
        "bin/uservm.elf",
    )

    #: Hooks that are auto-implemented in the base class and always
    #: available in the CLI, regardless of subclass overrides.
    AUTO_HOOKS: tuple[str, ...] = ("setup", "distclean", "lock", "help")

    #: Consumer-defined hooks that appear in the CLI only when the
    #: subclass overrides the corresponding method.
    CONSUMER_HOOKS: tuple[str, ...] = (
        "build",
        "test",
        "benchmark",
        "release",
        "clean",
    )

    def sysroot_required_files(self) -> list[str]:
        """Return the sysroot files required for the current deployment mode.

        Multi-process mode additionally requires ``linuxd.elf`` and
        ``uservm.elf``.  Subclasses can extend by overriding the class
        attributes or this method.
        """
        files = list(self.SYSROOT_REQUIRED_FILES)
        if self.config.deployment_mode == "multi-process":
            files.extend(self.SYSROOT_MULTI_PROCESS_FILES)
        return files

    def __init__(self, repo_root: Path) -> None:
        """Initialise ZScript for *repo_root*.

        Args:
            repo_root: Path to the consumer repository root.  Typically the
                directory that contains the ``.nanvix/`` folder.
        """
        self.repo_root = repo_root.resolve()
        self.nanvix_dir = self.repo_root / ".nanvix"
        self.config = Config(self.nanvix_dir)
        self.log = log
        self.targets: list[str] = []
        self.manifest: Manifest = load_manifest(self.nanvix_dir / "nanvix.toml")
        self.sysroot: Sysroot | None = None
        self.buildroot: Buildroot | None = None
        self.docker: DockerConfig | None = None
        self.combo_env: dict[str, str] | None = None

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

    def docker_image(self) -> str:
        """Return the default Docker image name for this build script.

        Override in a subclass to change the default.  The value is only
        used when ``--with-docker`` is passed; ``--docker-image`` and
        ``--with-minimal-docker`` bypass this method.

        Returns:
            Docker image reference string.
        """
        return DEFAULT_DOCKER_IMAGE

    def docker_config(self, image: str) -> DockerConfig:
        """Build the :class:`~nanvix_zutil.DockerConfig` for *image*.

        Constructs a standard configuration that mounts:

        * :attr:`repo_root` → ``/mnt/workspace`` (writable, workdir)
        * sysroot path from :attr:`config` → ``/mnt/sysroot`` (read-only),
          if the sysroot has been configured
        * ``.nanvix/buildroot`` → ``/mnt/buildroot`` (read-only),
          if the buildroot directory exists on disk

        Override in a subclass to add extra mounts or environment variables.

        Args:
            image: Docker image name to use.

        Returns:
            A fully populated :class:`~nanvix_zutil.DockerConfig`.
        """
        mounts: list[Mount] = [
            Mount(
                host_path=self.repo_root,
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

        buildroot_dir = self.nanvix_dir / "buildroot"
        if buildroot_dir.is_dir():
            mounts.append(
                Mount(
                    host_path=buildroot_dir,
                    container_path=BUILDROOT_CONTAINER_PATH,
                    readonly=True,
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
        if self.docker is not None:
            return self.docker.translate_path(host_path)
        return host_path

    # ------------------------------------------------------------------
    # Lifecycle hooks — auto-implemented
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Prepare the build environment.

        The base implementation automatically downloads the Nanvix sysroot
        and all build-time dependencies declared in ``nanvix.toml``, then
        saves the resulting paths to :attr:`config`.

        Subclasses may override this to perform additional setup steps.
        Call ``super().setup()`` to retain the automatic download behaviour::

            def setup(self) -> None:
                super().setup()
                # extra verification or configuration here
        """
        self.sysroot = Sysroot.download(
            machine=self.config.machine,
            deployment_mode=self.config.deployment_mode,
            memory_size=self.config.memory_size,
            tag=self.manifest.sysroot_ref.value,
            gh_token=self.config.get(CFG_GH_TOKEN),
            dest=self.nanvix_dir / "sysroot",
            config=self.config,
        )
        self.sysroot.verify(self.sysroot_required_files())
        self.config.set(CFG_SYSROOT, str(self.sysroot.path))

        # Deferred auto-suffix: when sysroot is "latest", load_manifest()
        # skips suffixing because the real version isn't known yet.  Now
        # that the sysroot is resolved, suffix VERSION deps before
        # passing them to install_dep().
        deps: list[Dependency] = list(self.manifest.dependencies)
        if self.manifest.sysroot_ref.value == "latest":
            if not self.sysroot.tag:
                log.fatal(
                    "Sysroot resolved to 'latest' but no tag is available"
                    " — delete .nanvix/sysroot and re-run 'nanvix-zutil setup'.",
                    code=EXIT_MISSING_DEP,
                )
            resolved_version = self.sysroot.tag.removeprefix("v")
            deps = [suffix_dep(d, resolved_version) for d in deps]

        if deps:
            self.buildroot = Buildroot.create(self.nanvix_dir / "buildroot")
            for dep in deps:
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
                            requested_ver = extract_nanvix_version(str(dep.ref.value))
                            log.info(
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

    def distclean(self) -> None:
        """Remove all transient ``.nanvix/`` artifacts.

        Deletes the ``sysroot``, ``buildroot``, ``cache``, ``venv``, and
        ``__pycache__`` directories and the ``env.json`` config file inside
        ``.nanvix/``.  Only the manifest (``nanvix.toml``) and lockfile
        (``nanvix.lock``) are preserved.

        Removal is best-effort: artifacts that cannot be deleted (e.g. a
        locked venv on Windows) are skipped with a warning so the
        remaining artifacts are still cleaned.
        """
        for artifact in (
            "sysroot",
            "buildroot",
            "cache",
            "env.json",
            "venv",
            "__pycache__",
            "_builds",
        ):
            path = self.nanvix_dir / artifact
            if not path.exists() and not path.is_symlink():
                continue
            try:
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
                else:
                    continue
                log.info(f"Removed {path}")
            except OSError as exc:
                log.warning(f"Could not remove {path}: {exc}")

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
            manifest_path=self.nanvix_dir / "nanvix.toml",
        )
        lock_path = self.nanvix_dir / "nanvix.lock"
        write_lockfile(lockfile, lock_path)
        log.success(f"Wrote {lock_path}")

    def lock_check(self) -> None:
        """Verify that ``nanvix.lock`` is up-to-date.

        Exits with ``EXIT_MISSING_DEP`` if the lockfile does not exist, or
        ``EXIT_INVALID_ARGS`` if it is stale relative to ``nanvix.toml``.
        """
        lock_path = self.nanvix_dir / "nanvix.lock"
        manifest_path = self.nanvix_dir / "nanvix.toml"
        lockfile = read_lockfile(lock_path)

        # Warn when "latest" lockfile may silently be stale.
        sysroot_pkg = next((p for p in lockfile.packages if p.name == "nanvix"), None)
        if sysroot_pkg is not None and sysroot_pkg.ref.value == "latest":
            log.warning(
                "'nanvix-version = \"latest\"' — lockfile staleness cannot"
                " be detected by hash; re-run 'nanvix-zutil lock' to pick up new"
                " releases."
            )

        if is_stale(lockfile, manifest_path):
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
                p = self.repo_root / name
                if p.is_file():
                    p.unlink()
                    log.info(f"Removed {name}")

    # ------------------------------------------------------------------
    # Subprocess helper
    # ------------------------------------------------------------------

    def run(
        self,
        *args: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        docker: bool = True,
        kvm: bool = False,
    ) -> "subprocess.CompletedProcess[str]":
        """Run a subprocess, logging the command before execution.

        When Docker mode is active (i.e. a ``--with-docker``,
        ``--with-minimal-docker``, or ``--docker-image`` flag was passed),
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
            kvm: When ``True`` and Docker is active, uses
                :meth:`~nanvix_zutil.DockerConfig.build_kvm_run_cmd` to add
                ``/dev/kvm`` access for functional tests.

        Returns:
            The completed process result.

        Raises:
            SystemExit: With exit code ``5`` if the process exits with a
                non-zero status.
        """
        if self.docker is not None and docker:
            cfg = self.docker
            # Merge combo env into Docker extra_env when in --all-builds mode.
            combo_and_explicit = {**(self.combo_env or {}), **(env or {})}
            if combo_and_explicit:
                merged = {**cfg.extra_env, **combo_and_explicit}
                cfg = dataclasses.replace(cfg, extra_env=merged)
            if kvm:
                if is_windows():
                    log.fatal(
                        "KVM mode is not available on Windows",
                        code=EXIT_BUILD_FAILURE,
                        hint="KVM requires a Linux host with /dev/kvm access.",
                    )
                cmd = cfg.build_kvm_run_cmd(*args)
            elif is_windows() and (cfg.crlf_files or cfg.output_files):
                cmd = cfg.build_windows_run_cmd(*args)
            else:
                cmd = cfg.build_run_cmd(*args)
            working_dir = self.repo_root
            subprocess_env: dict[str, str] | None = None
        else:
            cmd = list(args)
            working_dir = cwd if cwd is not None else self.repo_root
            # When combo_env is set (--all-builds mode), merge it into the
            # subprocess env so subprocesses see the correct NANVIX_* values
            # for this combo.  This mirrors the Docker branch behaviour.
            if env is not None:
                subprocess_env = {**(self.combo_env or {}), **env}
            elif self.combo_env:
                subprocess_env = {**os.environ, **self.combo_env}
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
    def main(cls, *, repo_root: Path | None = None) -> None:
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
        # Also pre-parse --mode / --all-builds so that --mode can override
        # NANVIX_DEPLOYMENT_MODE before Config.__init__ runs.
        pre_parser = argparse.ArgumentParser(add_help=False)
        pre_parser.add_argument("--json", action="store_true", default=False)
        pre_parser.add_argument(
            "--version",
            action="version",
            version=f"%(prog)s (nanvix-zutil {get_zutil_version()})",
        )
        pre_parser.add_argument("--mode", default=None, dest="mode")
        pre_parser.add_argument(
            "--all-builds", action="store_true", default=False, dest="all_builds"
        )
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

        # Infer repo root: the parent of the .nanvix/ directory.
        if repo_root is None:
            script_path = Path(sys.argv[0]).resolve()
            # z.py lives at <repo>/.nanvix/z.py → repo_root is two levels up.
            if script_path.parent.name == ".nanvix":
                repo_root = script_path.parent.parent
            else:
                repo_root = script_path.parent

        # ------------------------------------------------------------------
        # Handle --mode without --all-builds: override NANVIX_DEPLOYMENT_MODE
        # before Config.__init__ reads it during instance construction.
        # ------------------------------------------------------------------
        cli_mode = getattr(pre_args, "mode", None)
        if cli_mode is not None and not getattr(pre_args, "all_builds", False):
            os.environ["NANVIX_DEPLOYMENT_MODE"] = cli_mode

        instance = cls(repo_root)
        instance.targets = targets

        # Build the parser dynamically: auto hooks are always registered;
        # consumer hooks only appear when the subclass overrides them.
        parser = build_parser(available=instance.available_subcommands())
        args = parser.parse_args(framework_argv)

        # ------------------------------------------------------------------
        # Resolve Docker image from CLI flags.
        # ------------------------------------------------------------------
        docker_image: str | None = None
        if getattr(args, "docker_image", None):
            docker_image = args.docker_image
        elif getattr(args, "with_minimal_docker", False):
            docker_image = DEFAULT_DOCKER_IMAGE
        elif getattr(args, "with_docker", False):
            docker_image = instance.docker_image()

        if docker_image is not None:
            if not docker_available():
                log.fatal(
                    "Docker is not available — install Docker or omit the"
                    " --with-docker / --docker-image flag.",
                    code=EXIT_MISSING_DEP,
                )
            if not image_exists(docker_image):
                log.fatal(
                    f"Docker image '{docker_image}' not found locally."
                    f"  Pull it with: docker pull {docker_image}",
                    code=EXIT_MISSING_DEP,
                )
            instance.docker = instance.docker_config(docker_image)

        # ------------------------------------------------------------------
        # Multi-build mode (--all-builds)
        # ------------------------------------------------------------------
        if getattr(args, "all_builds", False):
            combos = expand_matrix(instance.manifest.builds)
            mode_filter = getattr(args, "mode", None)
            combos = filter_matrix(combos, mode=mode_filter)

            if not combos:
                log.fatal(
                    "No build combinations remain after filtering"
                    + (f" (--mode={mode_filter})" if mode_filter else ""),
                    code=EXIT_INVALID_ARGS,
                )

            subcommand_name: str | None = args.subcommand
            if subcommand_name is None:
                log.fatal(
                    "--all-builds requires a subcommand (e.g. ./z --all-builds test)",
                    code=EXIT_INVALID_ARGS,
                )

            # Only lifecycle hooks that are meaningful per-combo are allowed.
            _SUPPORTED_ALL_BUILDS = frozenset(
                {"setup", "build", "test", "benchmark", "release", "clean"}
            )
            if subcommand_name not in _SUPPORTED_ALL_BUILDS:
                log.fatal(
                    f"--all-builds is not supported with '{subcommand_name}'",
                    code=EXIT_INVALID_ARGS,
                )

            results = run_all_builds(
                script_cls=cls,
                combos=combos,
                hook=subcommand_name,
                targets=targets,
                docker_image=docker_image,
                repo_root=repo_root,
            )
            print_summary(results)

            failed = sum(1 for r in results.values() if not r.success)
            if failed:
                failure_code = (
                    EXIT_TEST_FAILURE
                    if subcommand_name in {"test", "benchmark"}
                    else EXIT_BUILD_FAILURE
                )
                log.fatal(
                    f"{failed}/{len(results)} build(s) failed",
                    code=failure_code,
                )
            log.success(f"All {len(results)} build(s) passed")
            return

        subcommand: str | None = args.subcommand

        # Special handling for lock subcommand (--check, --shallow flags).
        if subcommand == "lock":
            if args.check:
                instance.lock_check()
                log.success("Lockfile is up-to-date")
            else:
                instance.lock(shallow=args.shallow)
            return

        dispatch: dict[str, object] = {
            "setup": instance.setup,
            "distclean": instance.distclean,
            "build": instance.build,
            "test": instance.test,
            "benchmark": instance.benchmark,
            "release": instance.release,
            "clean": instance.clean,
        }

        handler = dispatch.get(subcommand) if subcommand is not None else None
        if callable(handler) and subcommand is not None:
            handler()
            log.success(f"{subcommand.capitalize()} complete")
        else:
            log.fatal(f"Unknown subcommand: {subcommand}", code=EXIT_INVALID_ARGS)
