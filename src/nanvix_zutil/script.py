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

import subprocess
import sys
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil.cli import build_parser
from nanvix_zutil.config import CFG_SYSROOT, Config
from nanvix_zutil.docker import (
    DEFAULT_DOCKER_IMAGE,
    SYSROOT_CONTAINER_PATH,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
    docker_available,
    image_exists,
)
from nanvix_zutil.exitcodes import (
    EXIT_BUILD_FAILURE,
    EXIT_INVALID_ARGS,
    EXIT_MISSING_DEP,
)
from nanvix_zutil.manifest import Manifest, load_manifest


class ZScript:
    """Base class for consumer build scripts.

    Provides CLI dispatch, config management, subprocess execution, and
    structured logging.  Consumers subclass this and implement the lifecycle
    hooks they need.

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
        self.docker: DockerConfig | None = None

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
        * sysroot path from :attr:`config` → ``/mnt/sysroot`` (read-only)

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

        return DockerConfig(
            image=image,
            mounts=mounts,
            workdir=WORKSPACE_CONTAINER_PATH,
        )

    # ------------------------------------------------------------------
    # Path translation helper
    # ------------------------------------------------------------------

    def translate_path(self, host_path: Path) -> Path:
        """Translate *host_path* to its container equivalent if Docker is active.

        When Docker mode is not active, returns *host_path* unchanged.

        Args:
            host_path: An absolute host-side path.

        Returns:
            Container-side :class:`~pathlib.Path` when Docker is active,
            otherwise *host_path*.
        """
        if self.docker is not None:
            return self.docker.translate_path(host_path)
        return host_path

    # ------------------------------------------------------------------
    # Lifecycle hooks — override in subclass
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Prepare the build environment.

        Override to download sysroot / buildroot artifacts and persist
        their paths via :attr:`config`.
        """

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
                the current process environment.
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
            if kvm:
                cmd = self.docker.build_kvm_run_cmd(*args)
            else:
                cmd = self.docker.build_run_cmd(*args)
            working_dir = self.repo_root
        else:
            cmd = list(args)
            working_dir = cwd if cwd is not None else self.repo_root

        log.info(f"$ {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
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
        location of the calling script (``sys.argv[0]``).
        """
        parser = build_parser()

        # Split sys.argv on '--' to separate framework args from targets.
        argv = sys.argv[1:]
        if "--" in argv:
            sep = argv.index("--")
            framework_argv = argv[:sep]
            targets = argv[sep + 1 :]
        else:
            framework_argv = argv
            targets = []

        args = parser.parse_args(framework_argv)

        if args.json:
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

        subcommand: str | None = args.subcommand

        if subcommand is None or subcommand == "help":
            parser.print_help()
            return

        dispatch: dict[str, object] = {
            "setup": instance.setup,
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
