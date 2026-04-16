"""runner.py — Consumer runner for downstream_tests."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .fallback import export_fallback_env
from .log import dry, fail, log, ok
from .validation import validate_consumer
from .checkout import resolve_repo

# Shim for os.getuid / os.getgid: on Windows these don't exist, which
# crashes nanvix-zutil at import time.  The getattr is a no-op on Linux
# (returns the real function), so we always apply it for simplicity.
# TODO(nanvix/zutils#???): fix os.getuid/getgid usage in zutils itself
#   and remove this shim.
SHIM = (
    "import os,sys;"
    "os.getuid=getattr(os,'getuid',lambda:0);"
    "os.getgid=getattr(os,'getgid',lambda:0)"
)

# Timeout in seconds for subprocess calls.
_SUBPROCESS_TIMEOUT = 600


def run_consumer(
    consumer: str,
    repo_dir: Path,
    wheel_path: Path,
    *,
    setup_only: bool,
    force_fallback: bool,
    with_docker: bool,
    dry_run: bool,
) -> tuple[str, str]:
    """Run setup / build / test phases for one consumer.

    Creates a fresh venv inside ``repo_dir/.nanvix/venv``, installs the
    wheel, then runs the requested phases.  nanvix-zutil is invoked via
    a compatibility shim (no-op on Linux, patches os.getuid/getgid on
    Windows).

    Args:
        consumer:       GitHub ``owner/repo`` slug (used only for log output).
        repo_dir:       Working directory of the consumer repo.
        wheel_path:     Path to the nanvix-zutil wheel to install.
        setup_only:     Skip build and test phases.
        force_fallback: Force dependency fallback and assert it is triggered.
        with_docker:    Pass ``--with-docker`` to build / test phases.
        dry_run:        Print what would happen without executing.

    Returns:
        A ``(consumer, status)`` tuple where *status* is a human-readable
        result string, e.g. ``"OK (setup,build,test)"`` or
        ``"FAIL (setup)"``.
    """
    venv_dir = repo_dir / ".nanvix" / "venv"

    if dry_run:
        dry(f"  {consumer}: would create venv at {venv_dir}")
        dry(f"  {consumer}: would install wheel {wheel_path}")
        if force_fallback:
            dry(f"  {consumer}: would force dependency fallback")
        dry(f"  {consumer}: would run: nanvix-zutil setup")
        if not setup_only:
            docker_str = "--with-docker " if with_docker else ""
            dry(f"  {consumer}: would run: nanvix-zutil {docker_str}build")
            dry(f"  {consumer}: would run: nanvix-zutil {docker_str}test")
        return consumer, "OK (dry-run)"

    # --- venv creation ---------------------------------------------------------
    if venv_dir.exists():
        shutil.rmtree(venv_dir)

    log("  Creating venv and installing local wheel...")
    r = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        fail(f"  {consumer}: venv creation failed")
        return consumer, "FAIL (venv)"

    # Locate python inside the venv.
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.exists():
        venv_python = venv_dir / "Scripts" / "python"

    # Install wheel.
    pip_r = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", str(wheel_path)],
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    if pip_r.returncode != 0:
        # Try ensurepip first.
        subprocess.run(
            [str(venv_python), "-m", "ensurepip", "--default-pip"],
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
        pip_r2 = subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--quiet", str(wheel_path)],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
        if pip_r2.returncode != 0:
            fail(f"  {consumer}: wheel install failed")
            return consumer, "FAIL (pip install)"

    ver = subprocess.run(
        [str(venv_python), "-c", "import nanvix_zutil; print('OK')"],
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    log(f"  nanvix_zutil import: {ver.stdout.strip()}")

    # --- Force fallback setup --------------------------------------------------
    if force_fallback:
        log("  Forcing dependency fallback...")
        buildroot = repo_dir / ".nanvix" / "buildroot"
        cache = repo_dir / ".nanvix" / "cache"
        if buildroot.exists():
            shutil.rmtree(buildroot)
        if cache.exists():
            shutil.rmtree(cache)
        log("  Cleaned buildroot and cache")
        export_fallback_env(repo_dir)

    # --- Phase 1: setup --------------------------------------------------------
    log("  Running: nanvix-zutil setup")
    setup_cmd = [
        str(venv_python),
        "-c",
        f"{SHIM};from nanvix_zutil.__main__ import main;sys.exit(main())",
        "setup",
    ]

    setup_result = subprocess.run(
        setup_cmd,
        capture_output=True,
        text=True,
        cwd=str(repo_dir),
        timeout=_SUBPROCESS_TIMEOUT,
    )
    setup_output = setup_result.stdout + setup_result.stderr
    print(setup_output, end="")
    setup_rc = setup_result.returncode

    if force_fallback:
        fallback_detected = setup_rc == 7 or bool(
            re.search(r"fallback for", setup_output, re.IGNORECASE)
        )
        if fallback_detected:
            ok(f"  {consumer} setup: fallback detected (exit {setup_rc})")
            return consumer, "OK (fallback verified)"
        if setup_rc == 0:
            fail(f"  {consumer} setup: no fallback detected (exit 0, no fallback log)")
            return consumer, "FAIL (fallback not triggered)"
        fail(f"  {consumer} setup: unexpected exit {setup_rc} (no fallback log)")
        return consumer, f"FAIL (setup exit {setup_rc})"

    if setup_rc != 0:
        fail(f"  {consumer} setup failed")
        return consumer, "FAIL (setup)"
    ok(f"  {consumer} setup: OK")

    if setup_only:
        log("  (skipping build/test — setup-only mode)")
        return consumer, "OK (setup)"

    # --- Docker availability check ---------------------------------------------
    docker_flag: list[str] = []
    if with_docker:
        if not shutil.which("docker"):
            log("  --with-docker requested but Docker not available — skipping build/test")
            return consumer, "OK (setup, no docker)"
        docker_flag = ["--with-docker"]

    # --- Phase 2: build --------------------------------------------------------
    build_cmd = [
        str(venv_python),
        "-c",
        f"{SHIM};from nanvix_zutil.__main__ import main;sys.exit(main())",
        *docker_flag,
        "build",
    ]

    log(f"  Running: nanvix-zutil {' '.join(docker_flag)} build".rstrip())
    build_result = subprocess.run(
        build_cmd,
        capture_output=True,
        text=True,
        cwd=str(repo_dir),
        timeout=_SUBPROCESS_TIMEOUT,
    )
    print(build_result.stdout + build_result.stderr, end="")
    if build_result.returncode != 0:
        fail(f"  {consumer} build failed")
        return consumer, "FAIL (build)"
    ok(f"  {consumer} build: OK")

    # --- Phase 3: test ---------------------------------------------------------
    test_cmd = [
        str(venv_python),
        "-c",
        f"{SHIM};from nanvix_zutil.__main__ import main;sys.exit(main())",
        *docker_flag,
        "test",
    ]

    log(f"  Running: nanvix-zutil {' '.join(docker_flag)} test".rstrip())
    test_result = subprocess.run(
        test_cmd,
        capture_output=True,
        text=True,
        cwd=str(repo_dir),
        timeout=_SUBPROCESS_TIMEOUT,
    )
    print(test_result.stdout + test_result.stderr, end="")
    if test_result.returncode != 0:
        fail(f"  {consumer} test failed")
        return consumer, "FAIL (test)"
    ok(f"  {consumer} test: OK")

    return consumer, "OK (setup,build,test)"


def run_consumers(
    consumers: list[dict],
    repos_root: Path,
    default_strategy: str,
    branch_pattern: str,
    wheel_path: Path,
    *,
    setup_only: bool,
    force_fallback: bool,
    with_docker: bool,
    dry_run: bool,
) -> int:
    """Iterate consumers, resolve repos, run phases, and report results.

    Args:
        consumers:        List of consumer dicts from the config (each has at
                          minimum a ``"repo"`` key).
        repos_root:       Root directory for consumer repo checkouts.
        default_strategy: Default checkout strategy from config.
        branch_pattern:   Default branch glob pattern from config.
        wheel_path:       Path to the built wheel.
        setup_only:       Skip build and test phases.
        force_fallback:   Force dependency fallback for all consumers.
        with_docker:      Pass ``--with-docker`` to build / test commands.
        dry_run:          Skip real operations; print what would happen.

    Returns:
        Number of consumers that failed.
    """
    log("=== Downstream Test ===")
    print()
    log(f"Wheel: {wheel_path}")
    log(f"Repos root: {repos_root}")
    log(f"Consumers: {', '.join(c['repo'] for c in consumers)}")
    log(f"Setup only: {setup_only}")
    print()

    results: list[tuple[str, str]] = []
    failed = 0

    for consumer_cfg in consumers:
        consumer = consumer_cfg["repo"]

        if not validate_consumer(consumer):
            fail(f"Invalid consumer name: '{consumer}' (must match owner/repo)")
            results.append((consumer, "FAIL (invalid name)"))
            failed += 1
            continue

        log(f"--- Testing {consumer} ---")

        # Per-consumer overrides.
        c_strategy: str = consumer_cfg.get("strategy", "") or default_strategy
        c_branch: str = consumer_cfg.get("branch", "") or ""
        c_path: str = consumer_cfg.get("path", "") or ""

        if c_path:
            repo_dir: Optional[Path] = Path(c_path)
        else:
            repo_dir = resolve_repo(
                consumer,
                repos_root,
                c_strategy,
                c_branch,
                branch_pattern,
                dry_run=dry_run,
            )

        if repo_dir is None:
            results.append((consumer, "FAIL (not found)"))
            failed += 1
            continue

        log(f"  Using: {repo_dir}")

        _, status = run_consumer(
            consumer,
            repo_dir,
            wheel_path,
            setup_only=setup_only,
            force_fallback=force_fallback,
            with_docker=with_docker,
            dry_run=dry_run,
        )
        results.append((consumer, status))
        if "FAIL" in status:
            failed += 1

    print()
    log("=== Results ===")
    for name, status in results:
        print(f"  {name}: {status}")
    print()

    if failed > 0:
        fail(f"{failed} consumer(s) FAILED")
    else:
        ok("All consumers passed!")

    return failed
