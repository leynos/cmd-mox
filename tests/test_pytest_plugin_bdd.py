"""Behavioural test of the cmd_mox pytest plug-in, expressed with pytest-bdd."""

from __future__ import annotations

import dataclasses as dc
import shutil
import textwrap
import typing as t
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

from tests.helpers.pytest_plugin import PARALLEL_SUITE, read_parallel_records

if t.TYPE_CHECKING:  # pragma: no cover - used for type checking only
    from _pytest.pytester import Pytester, RunResult

FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"

pytestmark = pytest.mark.requires_unix_sockets


@scenario(str(FEATURES_DIR / "pytest_plugin.feature"), "cmd_mox fixture basic usage")
def test_cmd_mox_plugin() -> None:
    """Bind scenario steps for the pytest plugin."""
    pass


@scenario(
    str(FEATURES_DIR / "pytest_plugin.feature"),
    "parallel tests use isolated resources",
)
def test_cmd_mox_parallel_isolation() -> None:
    """Bind scenario steps for the parallel isolation scenario."""
    pass


@dc.dataclass(slots=True)
class ParallelSuite:
    """Paths for the generated parallel isolation test suite."""

    test_file: Path
    artifact_dir: Path


TEST_CODE = textwrap.dedent(
    """
    import subprocess
    import pytest
    from cmd_mox.unittests.test_invocation_journal import _shim_cmd_path

    pytest_plugins = ("cmd_mox.pytest_plugin",)

    def test_example(cmd_mox):
        cmd_mox.stub("hello").returns(stdout="hi")
        res = subprocess.run(
            [str(_shim_cmd_path(cmd_mox, "hello"))],
            capture_output=True,
            text=True,
            check=True,
        )
        assert res.stdout.strip() == "hi"
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


@given(
    "a pytest suite exercising concurrent cmd_mox tests",
    target_fixture="parallel_suite",
)
def create_parallel_suite(pytester: Pytester) -> ParallelSuite:
    """Generate a pytest module that runs under multiple workers."""
    pytest.importorskip("xdist")
    artifact_dir = pytester.path / "artifacts"
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)
    test_file = pytester.makepyfile(PARALLEL_SUITE)
    return ParallelSuite(test_file=test_file, artifact_dir=artifact_dir)


@when("I run pytest with 2 workers", target_fixture="parallel_result")
def run_pytest_parallel(pytester: Pytester, parallel_suite: ParallelSuite) -> RunResult:
    """Execute the generated suite with pytest-xdist."""
    return pytester.runpytest("-n2", "-s", str(parallel_suite.test_file))


@then("each worker should use isolated resources")
def assert_parallel_isolation(
    parallel_suite: ParallelSuite, parallel_result: RunResult
) -> None:
    """Assert that shim directories and sockets are unique per worker."""
    parallel_result.assert_outcomes(passed=2)
    records = read_parallel_records(parallel_suite.artifact_dir)
    assert len(records) == 2
    assert {record.label for record in records} == {"alpha", "beta"}

    shim_dirs = {record.shim_dir for record in records}
    sockets = {record.socket for record in records}
    workers = {record.worker for record in records}

    assert len(shim_dirs) == len(records)
    assert len(sockets) == len(records)
    assert len(workers) == len(records)

    for record in records:
        assert record.socket.parent == record.shim_dir
        assert not record.shim_dir.exists()
        assert not record.socket.exists()

    shutil.rmtree(parallel_suite.artifact_dir)
