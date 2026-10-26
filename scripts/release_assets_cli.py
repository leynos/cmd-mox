#!/usr/bin/env python3
"""CLI for the release-asset reconciliation steps in ``release.yml``.

The workflow invokes this module from the repository root as
``python3 -m scripts.release_assets_cli``. It uses only the standard library;
all network calls remain in the workflow's ``curl`` and ``gh`` steps.
"""

from __future__ import annotations

import argparse
import sys
import typing as typ
from pathlib import Path

from scripts.release_assets import (
    ReleaseAssetError,
    State,
    carryover,
    check_fetched,
    missing_from_pypi,
    stage_github,
    stage_pypi,
    verify,
    write_output,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def _parser() -> argparse.ArgumentParser:
    """Build the command-line interface used by the release workflow.

    Returns
    -------
    argparse.ArgumentParser
        Parser for reconciliation commands and their staged input paths.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=_COMMANDS)
    parser.add_argument("--publish", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--github-files", type=Path)
    parser.add_argument("--bundles", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def _path(args: argparse.Namespace, option: str) -> Path:
    """Return a path option required by the selected command.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments.
    option : str
        The option name without its leading ``--``.

    Returns
    -------
    pathlib.Path
        The supplied path.

    Raises
    ------
    ReleaseAssetError
        When the selected command needs an option that was not supplied.
    """
    value = getattr(args, option)
    if value is None:
        msg = f"--{option.replace('_', '-')} is required for {args.command}"
        raise ReleaseAssetError(msg)
    return value


def _carryover(state: State, args: argparse.Namespace) -> None:
    """Print the names to download from the existing GitHub Release."""
    del args
    for name in carryover(state):
        sys.stdout.write(f"{name}\n")


def _stage_pypi(state: State, args: argparse.Namespace) -> None:
    """Stage PyPI's upload set and report whether any files remain."""
    write_output("remaining", value=stage_pypi(state, _path(args, "github_files")))


def _stage_github(state: State, args: argparse.Namespace) -> None:
    """Stage GitHub's upload set and print PyPI download rows as TSV."""
    output = _path(args, "output")
    fetch = stage_github(state, _path(args, "bundles"), output)
    for name, url in fetch:
        sys.stdout.write(f"{name}\t{url}\n")
    write_output("pending", value=bool(fetch) or any(output.iterdir()))


def _check_fetched(state: State, args: argparse.Namespace) -> None:
    """Check each fetched PyPI file against its indexed digest."""
    check_fetched(state, _path(args, "output"))


def _missing_pypi(state: State, args: argparse.Namespace) -> None:
    """Print each staged distribution absent from the current PyPI index."""
    del args
    for name in missing_from_pypi(state):
        sys.stdout.write(f"{name}\n")


def _verify(state: State, args: argparse.Namespace) -> None:
    """Fail if PyPI and the GitHub Release disagree about shipped files.

    Raises
    ------
    ReleaseAssetError
        When any release distribution or provenance bundle is missing.
    """
    problems = verify(state, _path(args, "bundles"))
    if problems:
        raise ReleaseAssetError("; ".join(problems))
    sys.stdout.write(
        "PyPI and the GitHub Release hold the same bytes for every artefact.\n"
    )


_COMMANDS: dict[str, cabc.Callable[[State, argparse.Namespace], None]] = {
    "carryover": _carryover,
    "stage-pypi": _stage_pypi,
    "stage-github": _stage_github,
    "check-fetched": _check_fetched,
    "missing-pypi": _missing_pypi,
    "verify": _verify,
}
_INDEX_COMMANDS = frozenset({
    "stage-pypi",
    "stage-github",
    "check-fetched",
    "missing-pypi",
    "verify",
})


def main(argv: cabc.Sequence[str] | None = None) -> int:
    """Run a reconciliation command and annotate handled failures.

    Parameters
    ----------
    argv : collections.abc.Sequence[str] or None, optional
        Arguments after the program name; defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        Zero on success, one for a handled release-asset error.
    """
    args = _parser().parse_args(argv)
    try:
        index = _path(args, "index") if args.command in _INDEX_COMMANDS else None
        state = State.load(args.publish, index, args.assets)
        _COMMANDS[args.command](state, args)
    except (ReleaseAssetError, OSError, KeyError, ValueError) as error:
        sys.stderr.write(f"::error title=release-assets::{error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
