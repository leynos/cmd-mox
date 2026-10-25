"""Configure imports and collection for the duplication-gate script tests."""

import collections.abc as cabc
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT_DIRECTORY = Path(__file__).resolve().parents[1]
# The directory must be appended, not prepended. Neither `tests/` nor this
# directory carries an `__init__.py`, so `tests` is a PEP 420 namespace package
# spanning both; putting `scripts/` first would make `tests.conftest` resolve to
# this file and break the importers of the root conftest's helpers. Reachability
# of the gate modules is already guaranteed by this entry plus the repository
# root being on `sys.path`.
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.append(str(SCRIPT_DIRECTORY))

# The gate's test modules import cyclopts, tomlkit, and hypothesis, and the gate
# entry point carries PEP 723 metadata selecting CPython 3.14. The application
# suite runs on the package's older interpreter and does not install those
# dependencies, so collecting them there would fail on the import rather than on
# an assertion. `collect_ignore` applies to directory discovery, which is what
# `testpaths` gives the application suite; `make duplication-test` names these
# files explicitly on the command line, which pytest exempts from the hook, so
# the gate's own target still runs them with its own environment.
collect_ignore = [
    "duplication_gate_test_support.py",
    "test_duplication_gate.py",
    "test_duplication_gate_commands.py",
    "test_duplication_gate_make.py",
    "test_duplication_gate_persistence.py",
    "test_duplication_gate_properties.py",
    "test_nose_detector.py",
]


@pytest.fixture
def write_pyproject(tmp_path: Path) -> cabc.Callable[[str], Path]:
    """Return a writer that puts a ``pyproject.toml`` under ``tmp_path``.

    Both the allowlist and the settings tests build a throwaway repository
    configuration. The writer dedents so a triple-quoted body can be indented
    with its surroundings in the test, and returns the path it wrote so the
    caller can hand it straight to the loader under test.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Per-test directory supplied by pytest.

    Returns
    -------
    collections.abc.Callable[[str], pathlib.Path]
        Writer taking the TOML body and returning the written path.
    """

    def _write(body: str) -> Path:
        """Write ``body`` to ``pyproject.toml`` and return the path."""  # ruff: ignore[docstring-missing-returns] - private test writer has an obvious path return
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(textwrap.dedent(body), encoding="utf-8")
        return pyproject

    return _write
