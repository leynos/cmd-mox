"""Steps for testing the pytest plugin."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import typing as t
from pathlib import Path

from behave import given, then, when  # type: ignore[attr-defined]


class BehaveContext(t.Protocol):
    """Behave step context for plugin tests."""

    test_file: Path
    tmpdir: Path
    result: subprocess.CompletedProcess[str]


@given("a temporary test file using the cmd_mox fixture")
def step_create_test_file(context: BehaveContext) -> None:
    """Write a pytest file that exercises the fixture."""
    test_code = """
import subprocess
import pytest

pytest_plugins = ("cmd_mox.pytest_plugin",)

def test_example(cmd_mox):
    cmd_mox.stub('hello').returns(stdout='hi')
    cmd_mox.replay()
    path = cmd_mox.environment.shim_dir / 'hello'
    res = subprocess.run(
        [str(path)], capture_output=True, text=True, check=True
    )
    assert res.stdout.strip() == 'hi'
    cmd_mox.verify()
"""
    tmpdir = Path(tempfile.mkdtemp())
    context.test_file = tmpdir / "test_example.py"
    context.tmpdir = tmpdir
    context.test_file.write_text(test_code)


@when("I run pytest on the file")
def step_run_pytest(context: BehaveContext) -> None:
    """Execute pytest on the generated file."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", str(context.test_file)],
        capture_output=True,
        text=True,
    )
    context.result = result
    shutil.rmtree(context.tmpdir)


@then("the run should pass")
def step_check_pass(context: BehaveContext) -> None:
    """Assert that pytest exited successfully."""
    assert context.result.returncode == 0  # noqa: S101
