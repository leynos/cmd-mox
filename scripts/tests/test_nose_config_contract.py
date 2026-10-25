"""Contract tests for the checked-in `[tool.nose]` scan configuration.

The gate reports a clean run when it scans nothing, so the settings in
`pyproject.toml` and the files they select are asserted here rather than
inferred from a passing `make duplication`. These tests read the production
configuration through `nose_detector.load_settings`, so they exercise the same
parse the gate performs. They need neither the pinned detector binary nor its
report schema, which keeps them runnable under `make duplication-test` without
`make install-nose` and under the application suite's own interpreter.
"""

from __future__ import annotations

import typing as typ

import nose_detector as detector

if typ.TYPE_CHECKING:
    from pathlib import Path

#: Characters that end a glob's leading literal text. The configured
#: exclusions are a literal subtree followed by `*`, so the text before the
#: first wildcard names the subtree the glob hides. Reading the glob that way
#: is an approximation of nose's gitignore matching, and it is deliberately
#: conservative: an over-broad reading can only weaken a "was hidden"
#: assertion, never manufacture one.
_WILDCARD_CHARACTERS = "*?[{"

#: The scan the adoption intends, frozen so that widening or narrowing it is a
#: deliberate edit here and in ADR 002 rather than a silent configuration
#: change. `tests/test_toolchain_contract.py` owns detector version drift; this
#: table covers only the fields that decide what is scanned.
_INTENDED_SCAN: typ.Final[dict[str, object]] = {
    "roots": ("cmd_mox",),
    "mode": "syntax,semantic,near",
    "min_size": 24,
    "surface": "all",
    "top": 30,
    "exclude": ("unittests/*",),
}


def _leading_literal(glob: str) -> str:
    """Return *glob*'s text before its first wildcard character."""  # ruff: ignore[docstring-missing-returns] - private helper has an obvious string return
    for index, character in enumerate(glob):
        if character in _WILDCARD_CHARACTERS:
            return glob[:index]
    return glob


def _is_hidden(relative: str, exclude: tuple[str, ...]) -> bool:
    """Report whether *relative* falls inside one of *exclude*'s subtrees."""  # ruff: ignore[docstring-missing-returns] - private predicate has an obvious boolean return
    return any(relative.startswith(_leading_literal(glob)) for glob in exclude)


def _selected_production_files(settings: detector.NoseSettings) -> tuple[Path, ...]:
    """Return the repository files the configured scan would consider."""  # ruff: ignore[docstring-missing-returns] - private selector has an obvious path-tuple return
    selected: list[Path] = []
    for root in settings.roots:
        root_directory = detector.REPO_ROOT / root
        for candidate in sorted(root_directory.rglob("*.py")):
            relative = candidate.relative_to(root_directory).as_posix()
            if not _is_hidden(relative, settings.exclude):
                selected.append(candidate)
    return tuple(selected)


def test_checked_in_settings_pin_the_intended_scan() -> None:
    """The production `[tool.nose]` table matches the intended scan."""
    settings = detector.load_settings(detector.PYPROJECT)
    actual = {
        "roots": settings.roots,
        "mode": settings.mode,
        "min_size": settings.min_size,
        "surface": settings.surface,
        "top": settings.top,
        "exclude": settings.exclude,
    }
    assert actual == _INTENDED_SCAN, (
        "the checked-in [tool.nose] scan changed; update ADR 002, the execplan "
        f"and this table together: {actual!r}"
    )


def test_each_configured_root_is_an_existing_directory_inside_the_repository() -> None:
    """Every configured root exists and resolves inside the repository."""
    settings = detector.load_settings(detector.PYPROJECT)
    repository_root = detector.REPO_ROOT.resolve()
    for root in settings.roots:
        resolved = (repository_root / root).resolve()
        assert resolved.is_dir(), f"configured nose root {root!r} is not a directory"
        assert resolved.is_relative_to(repository_root), (
            f"configured nose root {root!r} resolves outside the repository"
        )


def test_roots_select_production_python_outside_the_exclusions() -> None:
    """The scan still selects production modules, not an empty surface."""
    settings = detector.load_settings(detector.PYPROJECT)
    selected = _selected_production_files(settings)
    assert selected, "the configured roots and exclusions select no Python files"
    assert detector.REPO_ROOT / "cmd_mox" / "controller.py" in selected, (
        "the scan no longer selects cmd_mox/controller.py, so the configured "
        "roots or exclusions have drifted away from the production package"
    )


def test_each_exclusion_hides_python_files() -> None:
    """Every exclusion still removes files, so none has become vacuous."""
    settings = detector.load_settings(detector.PYPROJECT)
    for glob in settings.exclude:
        hidden = [
            candidate
            for root in settings.roots
            for candidate in sorted((detector.REPO_ROOT / root).rglob("*.py"))
            if _is_hidden(
                candidate.relative_to(detector.REPO_ROOT / root).as_posix(),
                (glob,),
            )
        ]
        assert hidden, (
            f"exclusion {glob!r} matches no Python file, so it no longer "
            "protects the scan from anything; confirm the subtree still exists"
        )
