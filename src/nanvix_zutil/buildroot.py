# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Build-time dependency descriptors for nanvix_zutil consumers.

:class:`Dependency` describes a single library fetched from a GitHub
release.  :class:`Ref` tags the version reference; the ``extract_*`` and
``suffix_dep`` helpers manipulate ``-nanvix-``-suffixed release tags.

Installation into the sysroot lives in
:class:`~nanvix_zutil.sysroot.Sysroot`.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from enum import Enum

from nanvix_zutil.release import DEV_ARCHIVE_SUFFIX

# ---------------------------------------------------------------------------
# Version reference
# ---------------------------------------------------------------------------


class RefKind(Enum):
    """Discriminator for version reference types."""

    TAG = "tag"
    COMMITISH = "commitish"
    ID = "id"
    VERSION = "version"
    LOCAL = "local"


@dataclass
class Ref:
    """A tagged union for version references.

    Attributes:
        kind: The specifier type — :attr:`RefKind.TAG` (exact tag match),
            :attr:`RefKind.COMMITISH` (match ``target_commitish``),
            :attr:`RefKind.ID` (direct release fetch),
            :attr:`RefKind.VERSION` (suffixed with nanvix version), or
            :attr:`RefKind.LOCAL` (filesystem path, no GitHub resolution).
        value: The version string, tag name, commitish, or release ID.
    """

    kind: RefKind
    value: str | int


# ---------------------------------------------------------------------------
# Dependency descriptor
# ---------------------------------------------------------------------------


@dataclass
class Dependency:
    """A library dependency fetched from a GitHub release.

    Attributes:
        name: Short library name (e.g. ``"zlib"``).
        repo: GitHub repository in ``owner/name`` format
            (e.g. ``"nanvix/zlib"``).
        ref: Version reference — one of tag, commitish, ID, or version.
        artifact_pattern: ``str.format``-style template for the asset file
            name.  Interpolated keys: ``{name}``, ``{host}``, ``{arch}``,
            ``{machine}``, ``{mode}``, ``{mem}``.  Default targets the
            standardised ``-dev`` archive produced by
            ``nanvix-zutil release``.
    """

    name: str
    repo: str
    ref: Ref
    artifact_pattern: str = (
        "{name}-{host}-{arch}-{machine}-{mode}-{mem}" + DEV_ARCHIVE_SUFFIX
    )


def suffix_dep(dep: Dependency, version: str) -> Dependency:
    """Return a copy of *dep* with its VERSION ref suffixed with *version*.

    If *dep* has a non-VERSION ref kind (e.g. TAG, COMMITISH, ID, or
    LOCAL), or its value already contains ``-nanvix-`` (e.g. from an
    env-var override), it is returned unchanged.

    Args:
        dep: The dependency to suffix.
        version: The nanvix sysroot version to append
            (e.g. ``"0.12.277"``).

    Returns:
        A new :class:`Dependency` with the suffixed ref, or the
        original if no suffixing is needed.
    """
    if dep.ref.kind == RefKind.VERSION and isinstance(dep.ref.value, str):
        if "-nanvix-" in dep.ref.value:
            return dep
        return _dc_replace(
            dep,
            ref=Ref(
                kind=dep.ref.kind,
                value=f"{dep.ref.value}-nanvix-{version}",
            ),
        )
    return dep


def extract_nanvix_version(suffixed_tag: str) -> str | None:
    """Extract the nanvix version from a suffixed tag.

    Given a tag like ``"1.3.1-nanvix-0.12.291"``, returns ``"0.12.291"``.
    Returns ``None`` if the tag does not contain the ``-nanvix-`` infix.

    Args:
        suffixed_tag: A release tag that may contain ``-nanvix-{version}``.

    Returns:
        The nanvix version string, or ``None``.
    """
    marker = "-nanvix-"
    idx = suffixed_tag.find(marker)
    if idx == -1:
        return None
    return suffixed_tag[idx + len(marker) :]


def extract_nanvix_version_base(suffixed_value: str) -> str | None:
    """Extract the base package version from a suffixed ref value.

    Given ``"1.3.1-nanvix-0.12.291"``, returns ``"1.3.1"``.
    Returns ``None`` if the value does not contain ``-nanvix-``.

    Args:
        suffixed_value: A ref value that may contain ``-nanvix-{version}``.

    Returns:
        The base package version string, or ``None``.
    """
    marker = "-nanvix-"
    idx = suffixed_value.find(marker)
    if idx == -1:
        return None
    return suffixed_value[:idx]


def parse_semver_tuple(version: str) -> tuple[int, ...]:
    """Parse a semver string into a tuple of integers for comparison.

    Args:
        version: A dotted version string (e.g. ``"0.12.291"``).

    Returns:
        Tuple of integer parts (e.g. ``(0, 12, 291)``).

    Raises:
        ValueError: If any part is not an integer.
    """
    return tuple(int(p) for p in version.split("."))
