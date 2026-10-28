#!/usr/bin/env python3
"""Check ``pyproject.toml``'s version against the release tag.

The release workflow runs this standard-library-only check before any job
attests or publishes distributions. It compares the public tag spelling
exactly and reports whether the version is a pre-release.

Examples
--------
>>> is_prerelease("0.2.0-beta1"), is_prerelease("1.2.3+rc")
(True, False)
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_LABEL = re.compile(r"preview|alpha|beta|post|pre|rev|dev|rc|a|b|c|r")
_RELEASE_SEGMENT = re.compile(r"^[0-9]+(?:\.[0-9]+)*")
_MINIMUM_RELEASE = (0, 2)
_PRERELEASE_LABELS = frozenset({
    "preview",
    "alpha",
    "beta",
    "pre",
    "dev",
    "rc",
    "a",
    "b",
    "c",
})


def is_prerelease(version: str) -> bool:
    """Return whether ``version`` has a public pre-release or dev label.

    Parameters
    ----------
    version : str
        A version string in PEP 440 spelling.

    Returns
    -------
    bool
        ``True`` when its public version has a pre-release or dev label.
        A local-only ``+`` suffix does not affect this result.

    Examples
    --------
    >>> is_prerelease("1.2.3"), is_prerelease("1.2.3rc1")
    (False, True)
    >>> is_prerelease("1.2.3+beta1")
    False
    """
    public = version.lower().partition("+")[0]
    return any(label in _PRERELEASE_LABELS for label in _LABEL.findall(public))


def _has_release_segment_after_historical(version: str) -> bool:
    """Return whether the numeric release segment is greater than 0.2.0.

    Returns
    -------
    bool
        Whether the normalized release segment follows the historical version.
    """
    match = _RELEASE_SEGMENT.match(version)
    if match is None:
        return False
    release = tuple(int(part) for part in match.group().split("."))
    while len(release) > 1 and release[-1] == 0:
        release = release[:-1]
    return release > _MINIMUM_RELEASE


def main(environ: cabc.Mapping[str, str], root: Path) -> int:
    """Compare the tag with the project version and write prerelease output.

    Parameters
    ----------
    environ : collections.abc.Mapping[str, str]
        Environment containing ``TAG_VERSION`` and ``GITHUB_OUTPUT``.
    root : pathlib.Path
        Repository root containing ``pyproject.toml``.

    Returns
    -------
    int
        Zero on an exact, eligible version match, otherwise one.
    """
    tag_version = environ["TAG_VERSION"].removeprefix("v")
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"]["version"]
    if declared != tag_version:
        sys.stderr.write(
            "::error file=pyproject.toml,title=Tag/pyproject.toml mismatch::"
            f"Tag version {tag_version} does not match pyproject.toml version "
            f"{declared}\n"
        )
        return 1
    if not _has_release_segment_after_historical(declared):
        sys.stderr.write(
            "::error file=pyproject.toml,title=Unsupported initial release::"
            "The first automated release must use a release segment later than "
            "0.2.0\n"
        )
        return 1
    prerelease = str(is_prerelease(declared)).lower()
    with Path(environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"prerelease={prerelease}\n")
    sys.stdout.write(f"Release tag {tag_version} matches pyproject.toml.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(os.environ, Path.cwd()))
