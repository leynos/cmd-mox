"""Unit tests for the pytest plugin."""

from __future__ import annotations

import dataclasses as dc
import textwrap
import typing as t

import pytest

from cmd_mox.controller import Phase
from cmd_mox.unittests import pytest_plugin_module_utils as plugin_utils
from cmd_mox.unittests.test_invocation_journal import _shim_cmd_path
from tests.helpers.pytest_plugin import load_parallel_suite, read_parallel_records

pytestmark = pytest.mark.requires_unix_sockets

LifecyclePhase = plugin_utils.LifecyclePhase

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import subprocess
    from pathlib import Path

    from cmd_mox.controller import CmdMox


@dc.dataclass(slots=True, frozen=True)
class AutoLifecycleTestCase:
    """Test case data for auto-lifecycle configuration scenarios."""

    config_method: str
    ini_setting: str | None
    cli_args: tuple[str, ...]
    test_decorator: str
    expected_phase: LifecyclePhase
    expect_auto_fail: bool = False


pytest_plugins = ("cmd_mox.pytest_plugin", "pytester")


def test_fixture_basic(
    cmd_mox: CmdMox,
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Fixture yields a CmdMox instance and cleans up."""
    cmd_mox.stub("hello").returns(stdout="hi")
    assert cmd_mox.phase is Phase.REPLAY
    cmd_path = _shim_cmd_path(cmd_mox, "hello")
    result = run([str(cmd_path)])
    assert result.stdout.strip() == "hi"


@pytest.mark.parametrize(
    ("worker_id", "expected_fragment"),
    [("gw99", "gw99"), (None, "main")],
    ids=["xdist-worker", "main-process"],
)
def test_worker_prefixes(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    worker_id: str | None,
    expected_fragment: str,
) -> None:
    """Worker ID is reflected in the environment prefix; falls back to 'main'."""
    _run_prefix_scenario(
        pytester,
        monkeypatch,
        worker_id=worker_id,
        expected_fragment=expected_fragment,
    )


def _run_prefix_scenario(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    *,
    worker_id: str | None,
    expected_fragment: str,
) -> None:
    """Execute a minimal test module and assert the shim prefix fragment."""
    if worker_id is None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    else:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", worker_id)

    test_module = textwrap.dedent(
        f"""
        from pathlib import Path
        from cmd_mox.unittests.conftest import run_subprocess

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def _shim_cmd_path(mox, name):
            sd = mox.environment.shim_dir
            assert sd is not None
            return Path(sd) / name

        def test_prefix(cmd_mox):
            cmd_mox.stub('foo').returns(stdout='bar')
            res = run_subprocess([str(_shim_cmd_path(cmd_mox, 'foo'))])
            assert res.stdout.strip() == 'bar'
            shim_base = Path(cmd_mox.environment.shim_dir).name
            assert '{expected_fragment}' in shim_base
        """
    )
    pytester.makepyfile(test_module)
    result = pytester.runpytest("-s")
    result.assert_outcomes(passed=1)


def test_parallel_workers_use_isolated_directories(
    pytester: pytest.Pytester, parallel_artifact_dir: Path
) -> None:
    """Parallel workers should each receive isolated shim directories and sockets."""
    pytest.importorskip("xdist")

    pytester.makepyfile(load_parallel_suite())
    result = pytester.runpytest("-n2", "-s")
    result.assert_outcomes(passed=2)

    records = read_parallel_records(parallel_artifact_dir)
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


def test_missing_invocation_fails_during_teardown(pytester: pytest.Pytester) -> None:
    """Verification failures should fail the test even without explicit calls."""
    test_file = pytester.makepyfile(
        """
        import pytest

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def test_missing_invocation(cmd_mox):
            cmd_mox.mock("hello").returns(stdout="hi")
        """
    )

    result = pytester.runpytest(str(test_file))
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*UnfulfilledExpectationError*"])


def test_verification_error_suppressed_on_test_failure(
    pytester: pytest.Pytester,
) -> None:
    """Primary test failures should mask verification errors."""
    test_file = pytester.makepyfile(
        """
        import pytest

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def test_failure(cmd_mox):
            cmd_mox.mock("late").returns(stdout="ok")
            assert False
        """
    )

    result = pytester.runpytest(str(test_file))
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*assert False*"])


def test_teardown_error_reports_failure(pytester: pytest.Pytester) -> None:
    """Cleanup errors should fail the test with a helpful message."""
    test_file = pytester.makepyfile(
        """
        import pytest
        from cmd_mox.controller import CmdMox

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        @pytest.fixture(autouse=True)
        def break_exit(monkeypatch):
            def _boom(self, exc_type, exc, tb):
                raise OSError("kaboom")

            monkeypatch.setattr(CmdMox, "__exit__", _boom)

        def test_cleanup_error(cmd_mox):
            cmd_mox.stub("late").returns(stdout="ok")
        """
    )

    result = pytester.runpytest(str(test_file))
    result.assert_outcomes(passed=1, errors=1)
    expected = (
        "*cmd_mox teardown failure for "
        "test_teardown_error_reports_failure.py::test_cleanup_error: "
        "verification OSError: kaboom; cleanup for "
        "test_teardown_error_reports_failure.py::test_cleanup_error OSError: kaboom*"
    )
    result.stdout.fnmatch_lines([expected])


@pytest.mark.parametrize(
    "test_case",
    [
        pytest.param(
            AutoLifecycleTestCase(
                config_method="ini_disables",
                ini_setting="cmd_mox_auto_lifecycle = false",
                cli_args=(),
                test_decorator="",
                expected_phase=LifecyclePhase.RECORD,
                expect_auto_fail=False,
            ),
            id="ini-disables",
        ),
        pytest.param(
            AutoLifecycleTestCase(
                config_method="cli_disables",
                ini_setting=None,
                cli_args=("--no-cmd-mox-auto-lifecycle",),
                test_decorator="",
                expected_phase=LifecyclePhase.RECORD,
                expect_auto_fail=False,
            ),
            id="cli-disables",
        ),
        pytest.param(
            AutoLifecycleTestCase(
                config_method="marker_overrides_ini",
                ini_setting="cmd_mox_auto_lifecycle = false",
                cli_args=(),
                test_decorator="@pytest.mark.cmd_mox(auto_lifecycle=True)",
                expected_phase=LifecyclePhase.AUTO_FAIL,
                expect_auto_fail=True,
            ),
            id="marker-overrides-ini",
        ),
        pytest.param(
            AutoLifecycleTestCase(
                config_method="marker_overrides_cli",
                ini_setting=None,
                cli_args=("--no-cmd-mox-auto-lifecycle",),
                test_decorator="@pytest.mark.cmd_mox(auto_lifecycle=True)",
                expected_phase=LifecyclePhase.REPLAY,
                expect_auto_fail=False,
            ),
            id="marker-overrides-cli",
        ),
        pytest.param(
            AutoLifecycleTestCase(
                config_method="fixture_param_bool",
                ini_setting=None,
                cli_args=(),
                test_decorator=(
                    '@pytest.mark.parametrize("cmd_mox", [False], indirect=True)'
                ),
                expected_phase=LifecyclePhase.RECORD,
                expect_auto_fail=False,
            ),
            id="fixture-param-bool",
        ),
        pytest.param(
            AutoLifecycleTestCase(
                config_method="fixture_param_dict",
                ini_setting="cmd_mox_auto_lifecycle = false",
                cli_args=(),
                test_decorator="\n".join(
                    [
                        "@pytest.mark.parametrize(",
                        '    "cmd_mox", [{"auto_lifecycle": True}], indirect=True',
                        ")",
                    ]
                ),
                expected_phase=LifecyclePhase.REPLAY,
                expect_auto_fail=False,
            ),
            id="fixture-param-dict",
        ),
        pytest.param(
            AutoLifecycleTestCase(
                config_method="cli_overrides_ini",
                ini_setting="cmd_mox_auto_lifecycle = false",
                cli_args=("--cmd-mox-auto-lifecycle",),
                test_decorator="",
                expected_phase=LifecyclePhase.REPLAY,
                expect_auto_fail=False,
            ),
            id="cli-overrides-ini",
        ),
    ],
)
def test_auto_lifecycle_configuration(
    pytester: pytest.Pytester,
    test_case: AutoLifecycleTestCase,
) -> None:
    """Exercise lifecycle precedence without duplicating module scaffolding."""
    if test_case.ini_setting:
        pytester.makeini(
            textwrap.dedent(
                f"""
                [pytest]
                {test_case.ini_setting}
                """
            )
        )

    module = plugin_utils.generate_lifecycle_test_module(
        test_case.test_decorator,
        test_case.expected_phase,
        expect_auto_fail=test_case.expect_auto_fail,
    )
    module = f"# scenario: {test_case.config_method}\n" + module
    test_file = pytester.makepyfile(**{f"test_{test_case.config_method}.py": module})

    plugins: tuple[str, ...] = ("cmd_mox.pytest_plugin",) if test_case.cli_args else ()
    result = pytester.runpytest(*test_case.cli_args, str(test_file), plugins=plugins)

    if test_case.expect_auto_fail:
        result.assert_outcomes(passed=1, errors=1)
        result.stdout.fnmatch_lines(["*UnfulfilledExpectationError*"])
    else:
        result.assert_outcomes(passed=1)
