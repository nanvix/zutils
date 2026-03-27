# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""``nanvix-zutil`` CLI — unified entry point for all nanvix_zutil operations."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil.exitcodes import (
    EXIT_GENERAL_ERROR,
    EXIT_INVALID_ARGS,
    EXIT_MISSING_DEP,
)
from nanvix_zutil.lockfile import get_zutil_version
from nanvix_zutil.script import ZScript

# Subcommands that require .nanvix/z.py (consumer dispatch).
_CONSUMER_COMMANDS: frozenset[str] = frozenset(
    {
        "setup",
        "distclean",
        "build",
        "test",
        "benchmark",
        "release",
        "clean",
        "lock",
        "help",
    }
)

# Subcommands handled directly (standalone).
_STANDALONE_COMMANDS: frozenset[str] = frozenset({"info", "resolve"})

_ALL_COMMANDS: frozenset[str] = _CONSUMER_COMMANDS | _STANDALONE_COMMANDS


def discover_script_class(z_py: Path) -> type[ZScript]:
    """Import ``.nanvix/z.py`` and return the first ``ZScript`` subclass found.

    Args:
        z_py: Path to the consumer's ``z.py`` script.

    Returns:
        The ``ZScript`` subclass defined in the consumer script.
    """
    spec = importlib.util.spec_from_file_location("_z_consumer", z_py)
    if spec is None or spec.loader is None:
        log.fatal(f"Cannot load {z_py}", code=EXIT_GENERAL_ERROR)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_z_consumer"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    for attr in vars(module).values():
        if isinstance(attr, type) and issubclass(attr, ZScript) and attr is not ZScript:
            return attr

    log.fatal(
        f"No ZScript subclass found in {z_py}",
        code=EXIT_GENERAL_ERROR,
        hint="Define a class that inherits from ZScript in .nanvix/z.py.",
    )


def _print_help() -> None:
    """Print top-level help text."""
    version = get_zutil_version()
    standalone = sorted(_STANDALONE_COMMANDS)
    consumer = sorted(_CONSUMER_COMMANDS - {"help"})

    print(f"nanvix-zutil {version}")
    print()
    print("Usage: nanvix-zutil <command> [options]")
    print()
    print("Standalone commands (no .nanvix/z.py required):")
    for cmd in standalone:
        print(f"  {cmd}")
    print()
    print("Consumer commands (require .nanvix/z.py in working directory):")
    for cmd in consumer:
        print(f"  {cmd}")
    print()
    print("Options:")
    print("  --version    Show version and exit")
    print("  --help, -h   Show this help message")
    print()
    print("Run 'nanvix-zutil <command> --help' for command-specific help.")


def main() -> None:
    """Entry point for the ``nanvix-zutil`` command."""
    args = sys.argv[1:]

    # Determine the subcommand (first positional arg).
    positional = [a for a in args if not a.startswith("-")]
    subcmd = positional[0] if positional else None

    # --- Version flag ---
    if "--version" in args:
        print(f"nanvix-zutil {get_zutil_version()}")
        return

    # --- Help flag or no subcommand ---
    if subcmd is None or subcmd in ("-h", "--help"):
        _print_help()
        return

    if "-h" in args or "--help" in args:
        # Let the subcommand's own parser handle --help.
        pass

    # --- Standalone commands ---
    if subcmd == "info":
        from nanvix_zutil.info import main as info_main

        sys.argv = ["nanvix-zutil info", *args[args.index("info") + 1 :]]
        info_main()
        return

    if subcmd == "resolve":
        from nanvix_zutil.resolve_cmd import main as resolve_main

        sys.argv = ["nanvix-zutil resolve", *args[args.index("resolve") + 1 :]]
        resolve_main()
        return

    # --- Consumer commands ---
    z_py = Path.cwd() / ".nanvix" / "z.py"

    if subcmd in _CONSUMER_COMMANDS:
        if not z_py.exists():
            log.fatal(
                f".nanvix/z.py not found in {Path.cwd()}",
                code=EXIT_MISSING_DEP,
                hint="Run this command from a Nanvix consumer repo root.",
            )
        script_cls = discover_script_class(z_py)
        script_cls.main(repo_root=Path.cwd())
        return

    # --- Unknown subcommand ---
    log.fatal(f"Unknown command: {subcmd}", code=EXIT_INVALID_ARGS)


if __name__ == "__main__":
    main()
