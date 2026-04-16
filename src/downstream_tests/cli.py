"""cli.py — Command-line interface for downstream_tests."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import Any, Optional

from .config import ensure_config, load_config
from .wheel import build_wheel
from .runner import run_consumers
from .log import fail, ok

# scripts/downstream/ lives three levels above this file:
# src/downstream_tests/cli.py → src/downstream_tests/ → src/ → <repo_root>
# → <repo_root>/scripts/downstream/
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "downstream"


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed :class:`argparse.Namespace`.
    """
    parser = argparse.ArgumentParser(
        prog="downstream_tests",
        description="Validate nanvix-zutil against downstream consumers.",
    )
    parser.add_argument(
        "--repos-root",
        metavar="DIR",
        default=None,
        help=(
            "Root directory for consumer repo checkouts. "
            "Overrides the config's defaults.repos_root."
        ),
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        default=str(_SCRIPT_DIR / "downstream.json"),
        help="Path to downstream.json (default: <script_dir>/downstream.json).",
    )
    parser.add_argument(
        "--setup-only",
        action="store_true",
        default=False,
        help="Only run the setup phase.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        default=False,
        help="Skip wheel build; reuse an existing wheel.",
    )
    parser.add_argument(
        "--force-fallback",
        action="store_true",
        default=False,
        help="Force dependency-version fallback (implies --setup-only).",
    )
    parser.add_argument(
        "--with-docker",
        action="store_true",
        default=False,
        help="Pass --with-docker to nanvix-zutil build/test commands.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would happen without executing.",
    )
    parser.add_argument(
        "consumers",
        nargs="*",
        metavar="consumer",
        help="Owner/repo names to test (default: all from config).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for downstream_tests.

    Parses arguments, ensures / loads config, builds the wheel once, then
    runs all consumers.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Exit code: number of failed consumers (0 = all passed).
    """
    args = parse_args(argv)

    # --force-fallback implies --setup-only.
    if args.force_fallback:
        args.setup_only = True

    config_path = Path(args.config)
    cache_path = _SCRIPT_DIR / "consumer-repos.json"

    # Ensure config exists (auto-generate on first run).
    try:
        effective_config_path = ensure_config(
            config_path, cache_path, dry_run=args.dry_run
        )
    except RuntimeError as exc:
        fail(str(exc))
        return 1

    config: dict[str, Any] = load_config(effective_config_path)
    defaults: dict[str, Any] = config.get("defaults", {})

    # Resolve repos root: CLI flag > config default.
    if args.repos_root:
        repos_root = Path(args.repos_root).expanduser()
    else:
        repos_root = Path(str(defaults.get("repos_root", "~/repos"))).expanduser()

    default_strategy: str = str(defaults.get("checkout_strategy", "shallow"))
    branch_pattern: str = str(defaults.get("branch_pattern", "nanvix/v*"))

    # Filter consumers if specified on the CLI.
    all_consumers: list[dict[str, Any]] = config.get("consumers", [])
    if args.consumers:
        consumer_set = set(args.consumers)
        consumers: list[dict[str, Any]] = [c for c in all_consumers if c.get("repo") in consumer_set]
        # Also include any CLI-specified consumers not in config (they'll fail
        # validation downstream, giving a clear error message).
        config_repos = {c.get("repo") for c in all_consumers}
        for name in args.consumers:
            if name not in config_repos:
                consumers.append({"repo": name})
    else:
        consumers = all_consumers

    # Build wheel (once, shared across all consumers).
    zutils_root = Path.cwd()
    work_dir = Path(tempfile.gettempdir()) / "nanvix-downstream-test"

    wheel_path = build_wheel(
        zutils_root,
        work_dir,
        skip_build=args.skip_build,
        dry_run=args.dry_run,
    )

    failure_count = run_consumers(
        consumers,
        repos_root,
        default_strategy,
        branch_pattern,
        wheel_path,
        setup_only=args.setup_only,
        force_fallback=args.force_fallback,
        with_docker=args.with_docker,
        dry_run=args.dry_run,
    )

    print()
    if failure_count > 0:
        fail(f"Overall: {failure_count} failure(s)")
    else:
        ok("Overall: All consumers passed!")

    return failure_count
