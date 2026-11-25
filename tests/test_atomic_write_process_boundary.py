"""Process-boundary evidence that a killed writer keeps the previous document."""

from __future__ import annotations

import importlib
import json
import selectors
import signal
import subprocess
import sys
from pathlib import Path

import pytest

import cmd_mox

_REQUIRES_MODULE = pytest.mark.xfail(
    strict=True,
    raises=ModuleNotFoundError,
    reason="14.1.1: cmd_mox._atomic_write is not implemented yet",
)

_PREVIOUS_DOCUMENT = b'{\n  "previous": true\n}\n'

#: Runs in a child interpreter. It pauses inside the protocol, after the
#: temporary file is durable and closed, and before the rename.
_CHILD_SCRIPT = """
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])

from cmd_mox import _atomic_write


def blocked_replace(temporary, target):
    print("READY", flush=True)
    sys.stdin.read()


_atomic_write._replace = blocked_replace
_atomic_write.atomic_write_json(Path(sys.argv[1]), {"writer": "child"})
"""


def _wait_for_ready(process: subprocess.Popen[str]) -> bool:
    """Return whether the child reported that it paused before the rename."""
    stdout = process.stdout
    assert stdout is not None, "the child must expose its standard output"
    with selectors.DefaultSelector() as selector:
        selector.register(stdout, selectors.EVENT_READ)
        if not selector.select(timeout=10):
            return False
        return stdout.readline().strip() == "READY"


def _terminate(process: subprocess.Popen[str]) -> None:
    """Kill the child if it is still running and close all of its pipes."""
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()


@_REQUIRES_MODULE
@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX signals and pipes are required"
)
def test_killed_writer_leaves_previous_document(tmp_path: Path) -> None:
    """A SIGKILL before the rename leaves the previous complete document."""
    importlib.import_module("cmd_mox._atomic_write")
    target = tmp_path / "fixture.json"
    target.write_bytes(_PREVIOUS_DOCUMENT)
    repository_root = Path(cmd_mox.__file__).parents[1]

    with subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv runs this interpreter with an isolated child script
        [
            sys.executable,
            "-I",
            "-c",
            _CHILD_SCRIPT,
            str(target),
            str(repository_root),
        ],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    ) as process:
        try:
            assert _wait_for_ready(process), "the child never paused before the rename"
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=5)
        finally:
            _terminate(process)

        assert target.read_bytes() == _PREVIOUS_DOCUMENT, (
            "the previous document changed"
        )
        assert json.loads(target.read_text(encoding="utf-8")) == {"previous": True}, (
            "the target is not the previous document"
        )
        orphans = [
            entry
            for entry in tmp_path.iterdir()
            if entry.name.startswith(".fixture.json.") and entry.name.endswith(".tmp")
        ]
        assert len(orphans) == 1, "a hard kill leaves exactly one documented orphan"
