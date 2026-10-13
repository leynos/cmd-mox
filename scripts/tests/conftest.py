"""Configure imports and collection for the duplication-gate script tests."""

import sys
from pathlib import Path

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
