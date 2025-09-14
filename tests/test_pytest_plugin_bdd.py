"""Behavioural test of the cmd_mox pytest plug-in, expressed with pytest-bdd."""

from __future__ import annotations

import textwrap
import typing as t
from pathlib import Path

from pytest_bdd import given, scenario, then, when

if t.TYPE_CHECKING:  # pragma: no cover - used for type checking only
    from _pytest.pytester import Pytester, RunResult

FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"


@scenario(str(FEATURES_DIR / "pytest_plugin.feature"), "cmd_mox fixture basic usage")
def test_cmd_mox_plugin() -> None:
    """Bind scenario steps for the pytest plugin."""
    pass


TEST_CODE = textwrap.dedent(
    """
    import subprocess
    import pytest
    from cmd_mox.unittests.test_invocation_journal import _shim_cmd_path

    pytest_plugins = ("cmd_mox.pytest_plugin",)

    def test_example(cmd_mox):
        cmd_mox.stub("hello").returns(stdout="hi")
        cmd_mox.replay()
        res = subprocess.run(
            [_shim_cmd_path(cmd_mox, "hello")],
            capture_output=True,
            text=True,
            check=True,
        )
        assert res.stdout.strip() == "hi"
        cmd_mox.verify()
    """
)


@given("a temporary test file using the cmd_mox fixture", target_fixture="test_file")
def create_test_file(pytester: Pytester) -> Path:
    """Write the example test file."""
    return pytester.makepyfile(TEST_CODE)


@when("I run pytest on the file", target_fixture="result")
def run_pytest(pytester: Pytester, test_file: Path) -> RunResult:
    """Run the inner pytest instance."""
    return pytester.runpytest(str(test_file))


@then("the run should pass")
def assert_success(result: RunResult) -> None:
    """Assert that the test passed."""
    result.assert_outcomes(passed=1)
