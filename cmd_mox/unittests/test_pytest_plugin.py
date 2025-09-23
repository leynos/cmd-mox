"""Unit tests for the pytest plugin."""

from __future__ import annotations

import textwrap
import typing as t

import pytest

from cmd_mox.controller import Phase
from cmd_mox.unittests.test_invocation_journal import _shim_cmd_path

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import subprocess

    from cmd_mox.controller import CmdMox


pytest_plugins = ("cmd_mox.pytest_plugin", "pytester")


@pytest.mark.usefixtures("cmd_mox")
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


def test_worker_prefix(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker ID is included in the environment prefix."""
    _run_prefix_scenario(
        pytester, monkeypatch, worker_id="gw99", expected_fragment="gw99"
    )


def test_default_prefix(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fall back to 'main' when no worker ID is present."""
    _run_prefix_scenario(
        pytester, monkeypatch, worker_id=None, expected_fragment="main"
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
        import os
        from cmd_mox.unittests.conftest import run_subprocess

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def _shim_cmd_path(mox, name):
            sd = mox.environment.shim_dir
            assert sd is not None
            return sd / name

        def test_prefix(cmd_mox):
            cmd_mox.stub('foo').returns(stdout='bar')
            res = run_subprocess([str(_shim_cmd_path(cmd_mox, 'foo'))])
            assert res.stdout.strip() == 'bar'
            shim_dir = os.path.basename(cmd_mox.environment.shim_dir)
            assert '{expected_fragment}' in shim_dir
        """
    )
    pytester.makepyfile(test_module)
    result = pytester.runpytest("-s")
    result.assert_outcomes(passed=1)


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
    result.stdout.fnmatch_lines(["*cmd_mox fixture cleanup failed*"])


def test_disable_auto_lifecycle_via_ini(pytester: pytest.Pytester) -> None:
    """Global configuration can disable automatic replay/verify."""
    pytester.makeini(
        """
        [pytest]
        cmd_mox_auto_lifecycle = false
        """
    )

    test_file = pytester.makepyfile(
        """
        import pytest
        from cmd_mox.controller import Phase
        from cmd_mox.unittests.conftest import run_subprocess

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def _shim_cmd_path(mox, name):
            sd = mox.environment.shim_dir
            assert sd is not None
            return sd / name

        def test_manual(cmd_mox):
            assert cmd_mox.phase is Phase.RECORD
            cmd_mox.stub("tool").returns(stdout="ok")
            cmd_mox.replay()
            res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])
            assert res.stdout.strip() == "ok"
            cmd_mox.verify()
        """
    )

    result = pytester.runpytest(str(test_file))
    result.assert_outcomes(passed=1)


def test_disable_auto_lifecycle_via_cli(pytester: pytest.Pytester) -> None:
    """CLI option overrides the default automatic lifecycle."""
    test_file = pytester.makepyfile(
        """
        import pytest
        from cmd_mox.controller import Phase
        from cmd_mox.unittests.conftest import run_subprocess

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def _shim_cmd_path(mox, name):
            sd = mox.environment.shim_dir
            assert sd is not None
            return sd / name

        def test_manual(cmd_mox):
            assert cmd_mox.phase is Phase.RECORD
            cmd_mox.stub("tool").returns(stdout="ok")
            cmd_mox.replay()
            res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])
            assert res.stdout.strip() == "ok"
            cmd_mox.verify()
        """
    )

    result = pytester.runpytest(
        "-p",
        "cmd_mox.pytest_plugin",
        "--no-cmd-mox-auto-lifecycle",
        str(test_file),
    )
    result.assert_outcomes(passed=1)


def test_marker_overrides_ini(pytester: pytest.Pytester) -> None:
    """Per-test markers override the ini-driven lifecycle setting."""
    pytester.makeini(
        """
        [pytest]
        cmd_mox_auto_lifecycle = false
        """
    )

    test_file = pytester.makepyfile(
        """
        import pytest

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        @pytest.mark.cmd_mox(auto_lifecycle=True)
        def test_marker(cmd_mox):
            cmd_mox.mock("never-called").returns(stdout="nope")
        """
    )

    result = pytester.runpytest(str(test_file))
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*UnfulfilledExpectationError*"])


def test_marker_overrides_cli(pytester: pytest.Pytester) -> None:
    """Markers take precedence over conflicting CLI options."""
    test_file = pytester.makepyfile(
        """
        import pytest
        from cmd_mox.unittests.conftest import run_subprocess

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def _shim_cmd_path(mox, name):
            sd = mox.environment.shim_dir
            assert sd is not None
            return sd / name

        @pytest.mark.cmd_mox(auto_lifecycle=True)
        def test_marker(cmd_mox):
            cmd_mox.stub("tool").returns(stdout="ok")
            res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])
            assert res.stdout.strip() == "ok"
        """
    )

    result = pytester.runpytest(
        "-p",
        "cmd_mox.pytest_plugin",
        "--no-cmd-mox-auto-lifecycle",
        str(test_file),
    )
    result.assert_outcomes(passed=1)


def test_fixture_param_bool_disables_auto_lifecycle(pytester: pytest.Pytester) -> None:
    """Bool fixture parameters disable automatic replay/verify."""
    test_file = pytester.makepyfile(
        """
        import pytest
        from cmd_mox.controller import Phase
        from cmd_mox.unittests.conftest import run_subprocess

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def _shim_cmd_path(mox, name):
            sd = mox.environment.shim_dir
            assert sd is not None
            return sd / name

        @pytest.mark.parametrize("cmd_mox", [False], indirect=True)
        def test_manual(cmd_mox):
            assert cmd_mox.phase is Phase.RECORD
            cmd_mox.stub("tool").returns(stdout="ok")
            cmd_mox.replay()
            res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])
            assert res.stdout.strip() == "ok"
            cmd_mox.verify()
        """
    )

    result = pytester.runpytest(str(test_file))
    result.assert_outcomes(passed=1)


def test_fixture_param_dict_overrides_ini(pytester: pytest.Pytester) -> None:
    """Dict fixture parameters override ini defaults."""
    pytester.makeini(
        """
        [pytest]
        cmd_mox_auto_lifecycle = false
        """
    )

    test_file = pytester.makepyfile(
        """
        import pytest
        from cmd_mox.controller import Phase
        from cmd_mox.unittests.conftest import run_subprocess

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def _shim_cmd_path(mox, name):
            sd = mox.environment.shim_dir
            assert sd is not None
            return sd / name

        @pytest.mark.parametrize(
            "cmd_mox", [{"auto_lifecycle": True}], indirect=True
        )
        def test_param(cmd_mox):
            assert cmd_mox.phase is Phase.REPLAY
            cmd_mox.stub("tool").returns(stdout="ok")
            res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])
            assert res.stdout.strip() == "ok"
        """
    )

    result = pytester.runpytest(str(test_file))
    result.assert_outcomes(passed=1)


def test_cli_enables_auto_lifecycle_over_ini(pytester: pytest.Pytester) -> None:
    """CLI flag re-enables auto lifecycle when ini disables it."""
    pytester.makeini(
        """
        [pytest]
        cmd_mox_auto_lifecycle = false
        """
    )

    test_file = pytester.makepyfile(
        """
        import pytest
        from cmd_mox.controller import Phase
        from cmd_mox.unittests.conftest import run_subprocess

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        def _shim_cmd_path(mox, name):
            sd = mox.environment.shim_dir
            assert sd is not None
            return sd / name

        def test_cli(cmd_mox):
            assert cmd_mox.phase is Phase.REPLAY
            cmd_mox.stub("tool").returns(stdout="ok")
            res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])
            assert res.stdout.strip() == "ok"
        """
    )

    result = pytester.runpytest(
        "-p",
        "cmd_mox.pytest_plugin",
        "--cmd-mox-auto-lifecycle",
        str(test_file),
    )
    result.assert_outcomes(passed=1)
