"""
Helper utilities for ZScript implementers
"""

import dataclasses
import importlib.resources
import importlib.util
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from nanvix_zutil import log
from nanvix_zutil.config import CFG_SYSROOT
from nanvix_zutil.docker import DockerConfig, is_windows
from nanvix_zutil.exitcodes import EXIT_BUILD_FAILURE, EXIT_MISSING_DEP

if TYPE_CHECKING:
    from nanvix_zutil.script import ZScript


# Env vars that must never leak from the host into the Docker container.
#
# Forwarding ``PATH`` is the catastrophic case on Windows: the host value is a
# semicolon-separated list of ``C:\...`` paths with no ``/bin`` or ``/usr/bin``,
# which overrides the image's Linux ``PATH`` and makes runc fail to resolve the
# container's entrypoint (``exec: "sh": executable file not found in $PATH``).
# ``HOME`` is always set explicitly by ``DockerConfig`` (currently to ``/tmp``);
# ``USER`` is set explicitly on the standard (non-Windows) path.  In both cases
# the caller must not override them.  ``LD_LIBRARY_PATH`` and ``PYTHONPATH``
# similarly refer to host filesystem locations that do not exist inside the
# container.
_CONTAINER_ENV_BLOCKLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "USERNAME",
        "PWD",
        "OLDPWD",
        "SHELL",
        "TERM",
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "PYTHONPATH",
        "PYTHONHOME",
    }
)


def filter_container_env(env: dict[str, str]) -> dict[str, str]:
    """Strip host-only / shell-managed keys before forwarding into the container.

    The match is case-insensitive so that mixed-case variants (e.g. Windows'
    ``Path``) are also rejected.
    """
    return {
        k: v
        for k, v in env.items()
        if k not in _CONTAINER_ENV_BLOCKLIST
        and k.upper() not in _CONTAINER_ENV_BLOCKLIST
    }


def check_docker(image: str) -> None:
    """Ensure the Docker CLI and the requested *image* are available.

    Verifies that the ``docker`` binary is on ``PATH``, then runs
    ``docker image inspect`` to check whether *image* is present locally.
    If the image is missing, ``docker pull`` is invoked to fetch it.

    Args:
        image: Fully qualified Docker image reference to ensure is
            available locally.

    Raises:
        SystemExit: Calls :func:`log.fatal` with
            :data:`EXIT_MISSING_DEP` if the ``docker`` CLI is not on
            ``PATH`` or if the auto-pull of *image* fails.
    """

    if shutil.which("docker") is None:
        log.fatal(
            "Docker is not available. Install Docker to continue.",
            code=EXIT_MISSING_DEP,
        )

    image_exists = subprocess.run(["docker", "image", "inspect", image]).returncode == 0

    if not image_exists:
        res = subprocess.run(["docker", "pull", image]).returncode
        if res != 0:
            log.fatal(
                f"Docker image '{image}' could not be pulled.",
                code=EXIT_MISSING_DEP,
            )


def ensure_tool_installed(name: str):
    if importlib.util.find_spec(name) is None:
        log.fatal(
            f"{name} is not installed.",
            code=EXIT_MISSING_DEP,
            hint="Run ./z setup before using this command.",
        )


#: Mapping of canonical config filename -> destination relative to .nanvix/.
_CONFIG_FILES: dict[str, str] = {
    "pyrightconfig.json": "pyrightconfig.json",
    ".yamllint.yml": ".yamllint.yml",
    "black.toml": "black.toml",
    ".gitignore": ".gitignore",
}


def sync_configs(nanvix_dir: Path) -> None:
    """Sync canonical tool configuration files into ``.nanvix/``.

    Copies config files shipped inside ``nanvix_zutil.configs`` to the
    ``.nanvix/`` directory, ensuring all downstream repos use
    consistent linter/type-checker settings.  Files whose content
    already matches are skipped.  Configs are confined to ``.nanvix/``
    so consumer repo roots are never modified.
    """
    configs = importlib.resources.files("nanvix_zutil.configs")
    for src_name, dst_rel in _CONFIG_FILES.items():
        src = configs / src_name
        dst = nanvix_dir / dst_rel
        content = src.read_bytes()
        if dst.exists() and dst.read_bytes() == content:
            continue
        dst.write_bytes(content)
        log.note(f"Synced .nanvix/{dst_rel}")


@dataclass
class InitRdArgs:
    """
    Arguments passed to :func:`make_initrd`
    """

    app_args: list[str] | None = field(default=None)
    """ Optional CLI Arguments appended to the app's argv. """
    app_env: list[str] | None = field(default=None)
    """ Optional environment variables for the app as ``KEY=VALUE`` strings. """
    procd_args: list[str] | None = field(default=None)
    """ Optional CLI Arguments appended to ``procd``'s argv. """
    procd_env: list[str] | None = field(default=None)
    """ Optional environment variables for ``procd`` as ``KEY=VALUE`` strings. """
    memd_args: list[str] | None = field(default=None)
    """ Optional CLI Arguments appended to ``memd``'s argv. """
    memd_env: list[str] | None = field(default=None)
    """ Optional environment variables for ``memd`` as ``KEY=VALUE`` strings. """
    vfsd_args: list[str] | None = field(default=None)
    """ Optional CLI Arguments appended to ``vfsd``'s argv. """
    vfsd_env: list[str] | None = field(default=None)
    """ Optional environment variables for ``vfsd`` as ``KEY=VALUE`` strings. """
    kernel_args: list[str] | None = field(default=None)
    """ Optional arguments passed to the kernel via ``-kernel-args``. """
    bin_dir: Path | None = field(default=None)
    """ Directory containing the ELF binaries and ``mkimage``.  Defaults to the sysroot ``bin/`` directory. """


def make_initrd(instance: "ZScript", app: str, args: InitRdArgs = InitRdArgs()) -> Path:
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
        args: Additional arguments controlling the image generation.

    Returns:
        Path to the generated ``.img`` file.

    Raises:
        SystemExit: If *app* contains path separators, the sysroot
            is unavailable, or ``mkimage`` is missing.
    """
    # Validate that app is a bare filename — no directory components.
    if "/" in app or "\\" in app:
        log.fatal(
            f"app must be a bare filename without path separators, got: {app!r}",
            code=EXIT_MISSING_DEP,
        )

    if args.bin_dir is None:
        if instance.sysroot is not None:
            args.bin_dir = instance.sysroot.path / "bin"
        else:
            sysroot_str = instance.config.get(CFG_SYSROOT)
            if sysroot_str:
                args.bin_dir = Path(sysroot_str) / "bin"
            else:
                log.fatal(
                    "Sysroot not available; run setup first.",
                    code=EXIT_MISSING_DEP,
                )

    if is_windows():
        mkimage = args.bin_dir / "mkimage.exe"
    else:
        mkimage = args.bin_dir / "mkimage.elf"

    # Verify mkimage exists before attempting to run it.
    if not mkimage.exists():
        log.fatal(
            f"mkimage not found at {mkimage}; run setup first.",
            code=EXIT_MISSING_DEP,
            hint="Ensure the sysroot contains mkimage by running ./z setup.",
        )

    app_stem = Path(app).stem
    output = instance.repo_root / f"{app_stem}.img"

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

    if args.kernel_args:
        escaped = [_escape(a) for a in args.kernel_args]
        cmd.extend(["-kernel-args", " ".join(escaped)])

    cmd.extend(
        [
            _entry(
                args.bin_dir / "procd.elf", "procd", args.procd_args, args.procd_env
            ),
            _entry(args.bin_dir / "memd.elf", "memd", args.memd_args, args.memd_env),
            _entry(args.bin_dir / "vfsd.elf", "vfsd", args.vfsd_args, args.vfsd_env),
            _entry(instance.repo_root / app, app, args.app_args, args.app_env),
        ]
    )

    run(*cmd)

    return output


def run(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    docker: DockerConfig | None = None,
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
    subprocess_env: dict[str, str] | None = None
    if docker is not None:
        if env:
            # When the caller hands us ``dict(os.environ)`` (a common pattern
            # for autotools wrappers), the host's environment leaks into the
            # container via ``-e KEY=VALUE`` flags.  On Windows that includes
            # ``PATH`` (full of ``C:\...`` paths with no ``/bin`` or
            # ``/usr/bin``), which overrides the image's Linux ``PATH`` and
            # makes runc fail to resolve the container's entrypoint (e.g.
            # ``sh``) with ``exec: "sh": executable file not found in $PATH``.
            # Strip host-only / shell-managed keys before forwarding.
            merged = {**docker.extra_env, **filter_container_env(env)}
            docker = dataclasses.replace(docker, extra_env=merged)
        if is_windows():
            cmd = docker.build_windows_run_cmd(*args)
        else:
            cmd = docker.build_run_cmd(*args)
    else:
        cmd = list(args)
        if env is not None:
            subprocess_env = env
        else:
            subprocess_env = None

    log.info(f"$ {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
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
