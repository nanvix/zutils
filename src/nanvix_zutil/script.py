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
import importlib.resources
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

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
    docker_available,
    image_exists,
    is_windows,
)
from nanvix_zutil.exitcodes import (
    EXIT_BUILD_FAILURE,
    EXIT_DEGRADED_SETUP,
    EXIT_INVALID_ARGS,
    EXIT_MISSING_DEP,
)
from nanvix_zutil.github import resolve_release, resolve_release_with_fallback
from nanvix_zutil.lockfile import get_zutil_version, read_lockfile, write_lockfile
from nanvix_zutil.manifest import Manifest, load_manifest
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

    #: Hooks that are auto-implemented in the base class and always
    #: available in the CLI, regardless of subclass overrides.
    AUTO_HOOKS: tuple[str, ...] = (
        "setup",
        "distclean",
        "lock",
        "lint",
        "format",
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

    def sysroot_required_files(self) -> list[str]:
        """Return the sysroot files required for the current platform and mode.

        Uses Windows binary names (``nanvixd.exe``, ``mkramfs.exe``) on
        Windows; Linux names on other platforms.  Multi-process mode
        additionally requires ``linuxd.elf`` and ``uservm.elf``.
        Subclasses can extend by overriding the class attributes or
        this method.
        """
        if is_windows():
            files = list(self.SYSROOT_REQUIRED_FILES_WINDOWS)
        else:
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
        self._used_fallback: bool = False

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

    def setup(self) -> bool:
        """Prepare the build environment.

        The base implementation automatically downloads the Nanvix sysroot
        and all build-time dependencies declared in ``nanvix.toml``, then
        saves the resulting paths to :attr:`config`.

        Subclasses may override this to perform additional setup steps.
        Call ``super().setup()`` to retain the automatic download behaviour::

            def setup(self) -> bool:
                used_fallback = super().setup()
                # extra verification or configuration here
                return used_fallback

        Returns:
            ``True`` if any dependency was resolved via version fallback,
            ``False`` if all dependencies matched their exact requested
            versions.
        """
        self._used_fallback = False

        if self.manifest.sysroot_ref.kind == RefKind.LOCAL:
            self.sysroot = Sysroot.from_local(
                Path(str(self.manifest.sysroot_ref.value)),
                config=self.config,
            )
        else:
            self.sysroot = Sysroot.download(
                machine=self.config.machine,
                deployment_mode=self.config.deployment_mode,
                memory_size=self.config.memory_size,
                tag=self.manifest.sysroot_ref.value,
                gh_token=self.config.get(CFG_GH_TOKEN),
                dest=self.nanvix_dir / "sysroot",
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
        nanvix_local = os.environ.get("WITH_NANVIX")
        if nanvix_local:
            self.sysroot.overlay_local_nanvix(Path(nanvix_local))

        self.sysroot.verify(self.sysroot_required_files())

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

                # When --with-nanvix is active, try local artifacts first (only for nanvix-owned
                # dependencies).
                if (
                    nanvix_local
                    and dep.repo.startswith("nanvix/")
                    and self.buildroot.install_local_nanvix(dep, Path(nanvix_local))
                ):
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
        self._sync_configs()
        return self._used_fallback

    # ------------------------------------------------------------------
    # Config synchronisation
    # ------------------------------------------------------------------

    #: Mapping of canonical config filename -> destination relative to .nanvix/.
    _CONFIG_FILES: dict[str, str] = {
        "pyrightconfig.json": "pyrightconfig.json",
        ".yamllint.yml": ".yamllint.yml",
        "black.toml": "black.toml",
        ".gitignore": ".gitignore",
    }

    def _sync_configs(self) -> None:
        """Sync canonical tool configuration files into ``.nanvix/``.

        Copies config files shipped inside ``nanvix_zutil.configs`` to the
        ``.nanvix/`` directory, ensuring all downstream repos use
        consistent linter/type-checker settings.  Files whose content
        already matches are skipped.  Configs are confined to ``.nanvix/``
        so consumer repo roots are never modified.
        """
        configs = importlib.resources.files("nanvix_zutil.configs")
        for src_name, dst_rel in self._CONFIG_FILES.items():
            src = configs / src_name
            dst = self.nanvix_dir / dst_rel
            content = src.read_bytes()
            if dst.exists() and dst.read_bytes() == content:
                continue
            dst.write_bytes(content)
            log.note(f"Synced .nanvix/{dst_rel}")

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
    # Lint & format hooks — auto-implemented
    # ------------------------------------------------------------------

    def lint(self) -> None:
        """Run linters on ``.nanvix/*.py``.

        Runs ``black --check`` followed by ``pyright`` on all Python
        files in the ``.nanvix/`` directory.  Exits with
        ``EXIT_BUILD_FAILURE`` if either tool reports problems.
        """
        py_files = sorted(self.nanvix_dir.glob("*.py"))
        if not py_files:
            log.warning("No .py files found in .nanvix/ — nothing to lint")
            return
        str_files = [str(f) for f in py_files]
        black_cfg = str(self.nanvix_dir / "black.toml")
        pyright_cfg = str(self.nanvix_dir / "pyrightconfig.json")
        self._run_tool("black", "--config", black_cfg, "--check", *str_files)
        self._run_tool("pyright", "--project", pyright_cfg, *str_files)

    def format(self, *, check: bool = False) -> None:
        """Format ``.nanvix/*.py`` with black.

        Args:
            check: When ``True``, run ``black --check`` instead of
                auto-formatting (exit non-zero on diff).
        """
        py_files = sorted(self.nanvix_dir.glob("*.py"))
        if not py_files:
            log.warning("No .py files found in .nanvix/ — nothing to format")
            return
        str_files = [str(f) for f in py_files]
        black_cfg = str(self.nanvix_dir / "black.toml")
        cmd = ["black", "--config", black_cfg]
        if check:
            cmd.append("--check")
        cmd.extend(str_files)
        self._run_tool(*cmd)

    def _run_tool(self, *args: str) -> None:
        """Run a host-side tool via ``sys.executable -m``, exiting on failure."""
        import importlib.util as _imputil

        tool = args[0]
        if _imputil.find_spec(tool) is None:
            log.fatal(
                f"'{tool}' not found — install it with: "
                f"pip install nanvix-zutil[lint]",
                code=EXIT_MISSING_DEP,
            )
        cmd = [sys.executable, "-m", *args]
        log.info(f"$ {' '.join(args)}")
        result = subprocess.run(cmd, cwd=self.repo_root)
        if result.returncode != 0:
            log.fatal(
                f"{tool} failed with exit code {result.returncode}",
                code=EXIT_BUILD_FAILURE,
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
        if self.docker is not None and docker:
            cfg = self.docker
            if env:
                merged = {**cfg.extra_env, **env}
                cfg = dataclasses.replace(cfg, extra_env=merged)
            if is_windows():
                cmd = cfg.build_windows_run_cmd(*args)
            else:
                cmd = cfg.build_run_cmd(*args)
            working_dir = self.repo_root
            subprocess_env: dict[str, str] | None = None
        else:
            cmd = list(args)
            working_dir = cwd if cwd is not None else self.repo_root
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

        # Infer repo root: the parent of the .nanvix/ directory.
        if repo_root is None:
            script_path = Path(sys.argv[0]).resolve()
            # z.py lives at <repo>/.nanvix/z.py → repo_root is two levels up.
            if script_path.parent.name == ".nanvix":
                repo_root = script_path.parent.parent
            else:
                repo_root = script_path.parent

        # ------------------------------------------------------------------
        # Handle --mode: override NANVIX_DEPLOYMENT_MODE before Config
        # __init__ reads it during instance construction.
        # ------------------------------------------------------------------
        cli_mode = getattr(pre_args, "mode", None)
        if cli_mode is not None:
            os.environ["NANVIX_DEPLOYMENT_MODE"] = cli_mode

        instance = cls(repo_root)
        instance.targets = targets

        # Build the parser dynamically: auto hooks are always registered;
        # consumer hooks only appear when the subclass overrides them.
        parser = build_parser(available=instance.available_subcommands())
        args = parser.parse_args(framework_argv)

        # ------------------------------------------------------------------
        # Resolve Docker image from CLI flags or persisted config.
        #
        # setup requires --with-docker IMAGE explicitly.  build, release,
        # and clean load the persisted image from .nanvix/env.json (set
        # during setup).  If no persisted image exists the command fails.
        # test and benchmark run natively on the host.
        # ------------------------------------------------------------------
        docker_image: str | None = None
        subcommand_name_for_docker: str | None = args.subcommand

        #: Subcommands that always run inside Docker.
        _DOCKER_COMMANDS: frozenset[str | None] = frozenset(
            {"setup", "build", "release", "clean"}
        )

        # Subcommand-level flag (only present for setup — now required).
        with_docker_val = getattr(args, "with_docker", None)
        if isinstance(with_docker_val, str):
            docker_image = with_docker_val

        # For build/release/clean, load persisted image.
        #
        # Exception: on Windows, setup downloads host-native binaries
        # via the GitHub API and does not invoke Docker.  Skip Docker
        # entirely for setup on Windows — the image is still persisted
        # to .nanvix/env.json so that subsequent commands can use it,
        # but we do not require Docker to be installed or the image to
        # exist locally.
        _skip_docker = is_windows() and subcommand_name_for_docker == "setup"
        if (
            docker_image is None
            and subcommand_name_for_docker in _DOCKER_COMMANDS
            and not _skip_docker
        ):
            persisted_image = instance.config.get(CFG_DOCKER_IMAGE)
            if not persisted_image:
                log.fatal(
                    "No Docker image configured — run 'setup --with-docker IMAGE' first.",
                    code=EXIT_INVALID_ARGS,
                )
            docker_image = persisted_image

        if docker_image is not None and not _skip_docker:
            if not docker_available():
                log.fatal(
                    "Docker is not available — install Docker to continue.",
                    code=EXIT_MISSING_DEP,
                )
            if not image_exists(docker_image):
                log.fatal(
                    f"Docker image '{docker_image}' not found locally."
                    f"  Pull it with: docker pull {docker_image}",
                    code=EXIT_MISSING_DEP,
                )
            instance.docker = instance.docker_config(docker_image)

        # Persist Docker image on setup so subsequent commands
        # automatically use the same image.  This runs even on
        # Windows where Docker checks are skipped.
        if docker_image is not None and subcommand_name_for_docker == "setup":
            instance.config.set(CFG_DOCKER_IMAGE, docker_image)
            instance.config.save()

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

        # Special handling for format subcommand (--check flag).
        if subcommand == "format":
            instance.format(check=args.check)
            log.success("Format complete")
            return

        dispatch: dict[str, object] = {
            "setup": instance.setup,
            "distclean": instance.distclean,
            "build": instance.build,
            "test": instance.test,
            "benchmark": instance.benchmark,
            "release": instance.release,
            "clean": instance.clean,
            "lint": instance.lint,
        }

        handler = dispatch.get(subcommand) if subcommand is not None else None
        if callable(handler) and subcommand is not None:
            handler_result = handler()
            if subcommand == "setup":
                used_fallback = instance._used_fallback or bool(handler_result)
                instance._used_fallback = used_fallback
            else:
                used_fallback = False

            if subcommand == "setup" and used_fallback:
                log.warning(
                    f"{subcommand.capitalize()} complete with fallback dependencies",
                    code=EXIT_DEGRADED_SETUP,
                )
                sys.exit(EXIT_DEGRADED_SETUP)
            else:
                log.success(f"{subcommand.capitalize()} complete")
        else:
            log.fatal(f"Unknown subcommand: {subcommand}", code=EXIT_INVALID_ARGS)
