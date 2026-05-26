# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""``nanvix-zutil`` CLI — unified entry point for all nanvix_zutil operations."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil.cli import SUBCOMMAND_HELP, build_parser
from nanvix_zutil.config import ENV_VARS
from nanvix_zutil.exitcodes import (
    EXIT_GENERAL_ERROR,
    EXIT_INVALID_ARGS,
    EXIT_MISSING_DEP,
)
from nanvix_zutil.distclean_cmd import HELP as _DISTCLEAN_HELP
from nanvix_zutil.info import HELP as _INFO_HELP
from nanvix_zutil.lockfile import get_zutil_version
from nanvix_zutil.resolve_cmd import HELP as _RESOLVE_HELP
from nanvix_zutil.script import ZScript

# Standalone command help text, keyed by subcommand name.
_STANDALONE_HELP: dict[str, str] = {
    "distclean": _DISTCLEAN_HELP,
    "info": _INFO_HELP,
    "resolve": _RESOLVE_HELP,
}


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
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:
        sys.modules.pop("_z_consumer", None)
        log.fatal(
            f"Failed to import {z_py}",
            code=EXIT_GENERAL_ERROR,
            hint=f"Import error: {exc}",
        )

    for attr in vars(module).values():
        if isinstance(attr, type) and issubclass(attr, ZScript) and attr is not ZScript:
            return attr

    log.fatal(
        f"No ZScript subclass found in {z_py}",
        code=EXIT_GENERAL_ERROR,
        hint="Define a class that inherits from ZScript in .nanvix/z.py.",
    )


def _build_env_var_epilog() -> str:
    """Build the epilog string listing all recognised environment variables."""
    lines = ["", "environment variables:"]
    for var, desc in ENV_VARS.items():
        lines.append(f"  {var:<30s} {desc}")
    return "\n".join(lines)


def _build_top_level_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands registered.

    Returns:
        Fully configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="nanvix-zutil",
        description=(
            "Nanvix build orchestration utility.\n"
            "\n"
            "Consumer commands require a .nanvix/z.py file in the "
            "working directory.\n"
            "Standalone commands (distclean, info, resolve) work anywhere."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_build_env_var_epilog(),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"nanvix-zutil {get_zutil_version()}",
    )

    subparsers = parser.add_subparsers(dest="subcommand", title="commands")

    # Register consumer commands.
    for name in sorted(SUBCOMMAND_HELP):
        subparsers.add_parser(name, help=SUBCOMMAND_HELP[name], add_help=False)

    # Register standalone commands.
    for name in sorted(_STANDALONE_HELP):
        subparsers.add_parser(name, help=_STANDALONE_HELP[name], add_help=False)

    return parser


def main() -> None:
    """Entry point for the ``nanvix-zutil`` command."""
    parser = _build_top_level_parser()
    args, remaining = parser.parse_known_args()

    subcmd = args.subcommand

    # No subcommand → print help (or error on unknown args).
    if subcmd is None:
        if remaining:
            log.fatal(f"Unknown argument: {remaining[0]}", code=EXIT_INVALID_ARGS)
        parser.print_help()
        return

    help_requested = "-h" in remaining or "--help" in remaining

    # --- Standalone commands ---
    if subcmd == "distclean":
        from nanvix_zutil.distclean_cmd import main as distclean_main

        sys.argv = ["nanvix-zutil distclean", *remaining]
        distclean_main()
        return

    if subcmd == "info":
        from nanvix_zutil.info import main as info_main

        sys.argv = ["nanvix-zutil info", *remaining]
        info_main()
        return

    if subcmd == "resolve":
        from nanvix_zutil.resolve_cmd import main as resolve_main

        sys.argv = ["nanvix-zutil resolve", *remaining]
        resolve_main()
        return

    if subcmd == "help":
        parser.print_help()
        return

    # --- Consumer commands ---
    # --help / -h can be answered from the static parser without .nanvix/z.py.
    if help_requested:
        build_parser().parse_args([subcmd, "--help"])
        return

    z_py = Path.cwd() / ".nanvix" / "z.py"

    if not z_py.exists():
        log.fatal(
            f".nanvix/z.py not found in {Path.cwd()}",
            code=EXIT_MISSING_DEP,
            hint="Run this command from a Nanvix consumer repo root.",
        )
    script_cls = discover_script_class(z_py)
    script_cls.main(repo_root=Path.cwd())


if __name__ == "__main__":
    main()
