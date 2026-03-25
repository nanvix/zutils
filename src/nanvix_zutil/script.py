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
"""

from __future__ import annotations

import argparse
import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil.buildroot import Buildroot
from nanvix_zutil.cli import build_parser
from nanvix_zutil.config import CFG_GH_TOKEN, CFG_SYSROOT, Config
from nanvix_zutil.exitcodes import EXIT_BUILD_FAILURE, EXIT_INVALID_ARGS
from nanvix_zutil.manifest import Manifest, load_manifest
from nanvix_zutil.sysroot import Sysroot


def _get_version() -> str:
    """Return the installed ``nanvix-zutil`` version string.

    Returns:
        Version string (e.g. ``"0.1.0"``), or ``"unknown"`` if the package
        metadata is unavailable.
    """
    try:
        return importlib.metadata.version("nanvix-zutil")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


class ZScript:
    """Base class for consumer build scripts.

    Provides CLI dispatch, config management, subprocess execution, and
    structured logging.  Consumers subclass this and implement the lifecycle
    hooks they need.

    The :meth:`setup` and :meth:`distclean` hooks are *auto-implemented* in
    the base class and are always available in the CLI.  The remaining hooks
    (``build``, ``test``, ``benchmark``, ``release``, ``clean``) only appear
    in the help menu when the subclass overrides them.

    Attributes:
        SYSROOT_REQUIRED_FILES: Files that must exist in the sysroot
            regardless of deployment mode.  Override in subclasses to add
            library-specific files.
        SYSROOT_MULTI_PROCESS_FILES: Additional files required only for
            multi-process deployments (``linuxd.elf``, ``uservm.elf``).
        config: Persistent build configuration loaded from
            ``.nanvix/env.json`` and environment variables.
        repo_root: Absolute path to the consumer repository root.
        nanvix_dir: Absolute path to the ``.nanvix/`` directory.
        targets: Arguments passed after ``--`` on the command line.
            Lifecycle hooks can use these to customise behavior
            (e.g. ``./z test -- smoke integration``).
        sysroot: The :class:`~nanvix_zutil.Sysroot` downloaded by
            :meth:`setup`, or ``None`` before setup runs.
        buildroot: The :class:`~nanvix_zutil.Buildroot` populated by
            :meth:`setup`, or ``None`` when there are no dependencies.
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
    AUTO_HOOKS: tuple[str, ...] = ("setup", "distclean", "help")

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
        self.targets: list[str] = []
        self.manifest: Manifest = load_manifest(self.nanvix_dir / "nanvix.toml")
        self.sysroot: Sysroot | None = None
        self.buildroot: Buildroot | None = None

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

        if self.manifest.dependencies:
            self.buildroot = Buildroot.create(self.nanvix_dir / "buildroot")
            for dep in self.manifest.dependencies:
                self.buildroot.install_dep(
                    dep=dep,
                    machine=self.config.machine,
                    deployment_mode=self.config.deployment_mode,
                    memory_size=self.config.memory_size,
                    gh_token=self.config.get(CFG_GH_TOKEN),
                    sysroot_commitish=self.sysroot.commitish,
                )

        self.config.save()

    def distclean(self) -> None:
        """Remove all transient ``.nanvix/`` artifacts.

        Deletes the ``sysroot``, ``buildroot``, and ``cache`` directories
        inside ``.nanvix/``.  The manifest (``nanvix.toml``), saved config
        (``env.json``), and Python virtual environment (``venv/``) are
        preserved.
        """
        for artifact in ("sysroot", "buildroot", "cache"):
            path = self.nanvix_dir / artifact
            if path.exists():
                shutil.rmtree(path)
                log.info(f"Removed {path}")

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

        Override to clean generated files.
        """

    # ------------------------------------------------------------------
    # Subprocess helper
    # ------------------------------------------------------------------

    def run(
        self,
        *args: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> "subprocess.CompletedProcess[str]":
        """Run a subprocess, logging the command before execution.

        Args:
            *args: Command and arguments to execute.
            cwd: Working directory for the subprocess.  Defaults to
                :attr:`repo_root`.
            env: Environment variables for the subprocess.  ``None`` inherits
                the current process environment.

        Returns:
            The completed process result.

        Raises:
            SystemExit: With exit code ``5`` if the process exits with a
                non-zero status.
        """
        working_dir = cwd if cwd is not None else self.repo_root
        log.info(f"$ {' '.join(args)}")
        try:
            result = subprocess.run(
                list(args),
                cwd=working_dir,
                env=env,
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
    def main(cls) -> None:
        """Parse command-line arguments and dispatch to the appropriate
        lifecycle hook.

        Instantiates the class with the repository root inferred from the
        location of the calling script (``sys.argv[0]``).  The CLI parser
        is built *after* instantiation so that only the subcommands
        implemented by the concrete subclass are shown in ``--help``.
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
        pre_parser = argparse.ArgumentParser(add_help=False)
        pre_parser.add_argument("--json", action="store_true", default=False)
        pre_parser.add_argument(
            "--version",
            action="version",
            version=f"./z (nanvix-zutil {_get_version()})",
        )
        pre_args, _ = pre_parser.parse_known_args(framework_argv)

        if pre_args.json:
            log.set_json_mode(True)

        # Infer repo root: the parent of the .nanvix/ directory.
        script_path = Path(sys.argv[0]).resolve()
        # z.py lives at <repo>/.nanvix/z.py → repo_root is two levels up.
        if script_path.parent.name == ".nanvix":
            repo_root = script_path.parent.parent
        else:
            repo_root = script_path.parent

        instance = cls(repo_root)
        instance.targets = targets

        # Build the parser dynamically: auto hooks are always registered;
        # consumer hooks only appear when the subclass overrides them.
        parser = build_parser(available=instance.available_subcommands())
        args = parser.parse_args(framework_argv)

        subcommand: str | None = args.subcommand

        if subcommand is None or subcommand == "help":
            parser.print_help()
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

        handler = dispatch.get(subcommand)
        if callable(handler):
            handler()
            log.success(f"{subcommand.capitalize()} complete")
        else:
            log.fatal(f"Unknown subcommand: {subcommand}", code=EXIT_INVALID_ARGS)
