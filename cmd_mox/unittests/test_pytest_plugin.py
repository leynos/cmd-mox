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


@pytest.mark.parametrize(
    (
        "config_method",
        "ini_setting",
        "cli_args",
        "test_decorator",
        "expected_phase",
        "should_fail",
    ),
    [
        pytest.param(
            "ini_disables",
            "cmd_mox_auto_lifecycle = false",
            (),
            "",
            "RECORD",
            False,
            id="ini-disables",
        ),
        pytest.param(
            "cli_disables",
            None,
            ("-p", "cmd_mox.pytest_plugin", "--no-cmd-mox-auto-lifecycle"),
            "",
            "RECORD",
            False,
            id="cli-disables",
        ),
        pytest.param(
            "marker_overrides_ini",
            "cmd_mox_auto_lifecycle = false",
            (),
            "@pytest.mark.cmd_mox(auto_lifecycle=True)",
            "auto_fail",
            True,
            id="marker-overrides-ini",
        ),
        pytest.param(
            "marker_overrides_cli",
            None,
            ("-p", "cmd_mox.pytest_plugin", "--no-cmd-mox-auto-lifecycle"),
            "@pytest.mark.cmd_mox(auto_lifecycle=True)",
            "REPLAY",
            False,
            id="marker-overrides-cli",
        ),
        pytest.param(
            "fixture_param_bool",
            None,
            (),
            '@pytest.mark.parametrize("cmd_mox", [False], indirect=True)',
            "RECORD",
            False,
            id="fixture-param-bool",
        ),
        pytest.param(
            "fixture_param_dict",
            "cmd_mox_auto_lifecycle = false",
            (),
            "\n".join(
                [
                    "@pytest.mark.parametrize(",
                    '    "cmd_mox", [{"auto_lifecycle": True}], indirect=True',
                    ")",
                ]
            ),
            "REPLAY",
            False,
            id="fixture-param-dict",
        ),
        pytest.param(
            "cli_overrides_ini",
            "cmd_mox_auto_lifecycle = false",
            ("-p", "cmd_mox.pytest_plugin", "--cmd-mox-auto-lifecycle"),
            "",
            "REPLAY",
            False,
            id="cli-overrides-ini",
        ),
    ],
)
def test_auto_lifecycle_configuration(
    pytester: pytest.Pytester,
    config_method: str,
    ini_setting: str | None,
    cli_args: tuple[str, ...],
    test_decorator: str,
    expected_phase: str,
    *,
    should_fail: bool,
) -> None:
    """Exercise lifecycle precedence without duplicating module scaffolding."""
    if ini_setting:
        pytester.makeini(
            textwrap.dedent(
                f"""
                [pytest]
                {ini_setting}
                """
            )
        )

    module = _generate_lifecycle_test_module(
        test_decorator, expected_phase, should_fail=should_fail
    )
    module = f"# scenario: {config_method}\n" + module
    test_file = pytester.makepyfile(**{f"test_{config_method}.py": module})

    result = pytester.runpytest(*cli_args, str(test_file))

    if should_fail:
        result.assert_outcomes(passed=1, errors=1)
        result.stdout.fnmatch_lines(["*UnfulfilledExpectationError*"])
    else:
        result.assert_outcomes(passed=1)


def _generate_lifecycle_test_module(
    decorator: str, expected_phase: str, *, should_fail: bool
) -> str:
    """Return a self-contained test module for lifecycle precedence cases."""
    lines: list[str] = ["import pytest", "from cmd_mox.controller import Phase"]

    if expected_phase != "auto_fail":
        lines.append("from cmd_mox.unittests.conftest import run_subprocess")

    lines.extend(["", 'pytest_plugins = ("cmd_mox.pytest_plugin",)', ""])

    if expected_phase != "auto_fail":
        lines.extend(
            [
                "def _shim_cmd_path(mox, name):",
                "    sd = mox.environment.shim_dir",
                "    assert sd is not None",
                "    return sd / name",
                "",
            ]
        )

    if decorator:
        lines.extend(decorator.splitlines())
        lines.append("")

    lines.append("def test_case(cmd_mox):")

    if expected_phase == "RECORD":
        lines.extend(
            [
                "    assert cmd_mox.phase is Phase.RECORD",
                '    cmd_mox.stub("tool").returns(stdout="ok")',
                "    cmd_mox.replay()",
                '    res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])',
                '    assert res.stdout.strip() == "ok"',
                "    cmd_mox.verify()",
            ]
        )
    elif expected_phase == "REPLAY" and not should_fail:
        lines.extend(
            [
                "    assert cmd_mox.phase is Phase.REPLAY",
                '    cmd_mox.stub("tool").returns(stdout="ok")',
                '    res = run_subprocess([str(_shim_cmd_path(cmd_mox, "tool"))])',
                '    assert res.stdout.strip() == "ok"',
            ]
        )
    elif expected_phase == "auto_fail":
        lines.extend(
            [
                "    assert cmd_mox.phase is Phase.REPLAY",
                '    cmd_mox.mock("never-called").returns(stdout="nope")',
            ]
        )
    else:  # pragma: no cover - defensive guard for unexpected parameters
        msg = f"Unsupported expected_phase: {expected_phase}"
        raise ValueError(msg)

    lines.append("")
    return textwrap.dedent("\n".join(lines))
