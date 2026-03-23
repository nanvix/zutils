# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Declarative dependency file parser for ``nanvix-requirements.txt``.

Parses a simple text format where each non-blank, non-comment line is
either ``name`` or ``name@tag``.  When ``@tag`` is omitted the tag
defaults to ``"latest"``.  Environment variables ``NANVIX_VERSION``
(for the sysroot) and ``NANVIX_VERSION_<NAME>`` (for each dependency)
override whatever tag the file specifies.

The entry ``nanvix`` is **mandatory** and requires an explicit ``@tag``
(e.g. ``nanvix@latest`` or ``nanvix@<hash>``); all other entries become
:class:`~nanvix_zutil.buildroot.Dependency` objects under the
``nanvix/`` GitHub organisation.

Dependency tags that are not ``"latest"`` are automatically suffixed with
``-nanvix-{sysroot_tag}`` so that consumers only need to specify the
library version (e.g. ``zlib@b7a6a3c`` instead of
``zlib@b7a6a3c-nanvix-fa06b88``).  Tags that already contain
``-nanvix-`` are rejected to prevent accidental duplication.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil.buildroot import Dependency
from nanvix_zutil.exitcodes import EXIT_INVALID_ARGS, EXIT_MISSING_DEP

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Requirements:
    """Parsed contents of a ``nanvix-requirements.txt`` file.

    Attributes:
        sysroot_tag: Tag for the ``nanvix`` sysroot entry.
        dependencies: Non-sysroot entries as :class:`Dependency` objects.
    """

    sysroot_tag: str
    dependencies: list[Dependency] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_requirements(path: Path) -> Requirements:
    """Parse a ``nanvix-requirements.txt`` file.

    Each non-blank, non-comment line is ``name`` or ``name@tag``.  When
    ``@tag`` is omitted the tag defaults to ``"latest"``.  Environment
    variables ``NANVIX_VERSION`` (sysroot) and ``NANVIX_VERSION_<NAME>``
    (dependencies) take precedence over file-specified tags.

    The ``nanvix`` entry is mandatory and maps to the sysroot tag; all
    other entries become :class:`Dependency` objects.  Duplicate names
    are resolved as last-wins.

    Dependency tags that are not ``"latest"`` are automatically suffixed
    with ``-nanvix-{sysroot_tag}``.  Tags that already contain
    ``-nanvix-`` are rejected.

    Args:
        path: Path to the requirements file.

    Returns:
        A :class:`Requirements` instance.

    Raises:
        SystemExit: With exit code ``3`` if the file does not exist, or
            exit code ``2`` if the ``nanvix`` entry is missing.
    """
    if not path.is_file():
        log.fatal(
            f"Required file not found: {path}",
            code=EXIT_MISSING_DEP,
            hint="Create a nanvix-requirements.txt in the .nanvix/ directory.",
        )

    text = path.read_text(encoding="utf-8")

    sysroot_tag: str | None = None
    seen: dict[str, Dependency] = {}

    for raw_line in text.splitlines():
        # Strip inline comments.
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        if "@" in line:
            name, tag = line.split("@", 1)
            name = name.strip()
            tag = tag.strip()
            if not name:
                log.fatal(
                    f"{path}: empty dependency name in '{raw_line.strip()}'",
                    code=EXIT_INVALID_ARGS,
                    hint="Each line must be 'name' or 'name@tag'.",
                )
            if not tag:
                log.fatal(
                    f"{path}: empty tag for '{name}' in '{raw_line.strip()}'",
                    code=EXIT_INVALID_ARGS,
                    hint="Either remove the '@' to default to 'latest',"
                    " or specify a tag (e.g. 'latest' or a commit hash).",
                )
        else:
            name = line
            tag = "latest"

        if name == "nanvix":
            if tag == "latest" and "@" not in line:
                log.fatal(
                    f"{path}: 'nanvix' entry requires an explicit tag"
                    " (e.g. 'nanvix@latest' or 'nanvix@<hash>')",
                    code=EXIT_INVALID_ARGS,
                    hint="Write 'nanvix@latest' for the latest release,"
                    " or 'nanvix@<hash>' to pin a specific build.",
                )
            sysroot_tag = os.environ.get("NANVIX_VERSION", tag)
            continue

        env_key = f"NANVIX_VERSION_{name.upper()}"
        tag = os.environ.get(env_key, tag)

        if "-nanvix-" in tag:
            log.fatal(
                f"{path}: dependency '{name}' must not include the nanvix"
                f" version in its tag (got '{tag}')",
                code=EXIT_INVALID_ARGS,
                hint=f"Use '{name}@{tag.split('-nanvix-')[0]}' — the nanvix"
                " version is derived automatically from the 'nanvix' entry.",
            )

        dep = Dependency(name=name, repo=f"nanvix/{name}", tag=tag)
        seen[name] = dep

    if sysroot_tag is None:
        log.fatal(
            f"{path}: missing mandatory 'nanvix' entry",
            code=EXIT_INVALID_ARGS,
            hint="Add a 'nanvix' line to the requirements file.",
        )

    # Auto-suffix dependency tags with the nanvix version so that
    # consumers write e.g. ``zlib@b7a6a3c`` instead of the full
    # ``zlib@b7a6a3c-nanvix-fa06b88``.
    for dep in seen.values():
        if dep.tag != "latest":
            dep.tag = f"{dep.tag}-nanvix-{sysroot_tag}"

    return Requirements(sysroot_tag=sysroot_tag, dependencies=list(seen.values()))
